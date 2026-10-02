"""Named account vault with one active profile and no implicit credential fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock
from platformdirs import user_config_path, user_data_path

SERVER = "https://game.battlecode.au"
API_URL = f"{SERVER}/api/v1"
KEY_PATTERN = re.compile(r"^bc_[A-Za-z0-9_-]+$")
SERVICE = "battlecode-cli"


def config_dir() -> Path:
    return user_config_path("battlecode-cli", appauthor=False)


def data_dir() -> Path:
    return user_data_path("battlecode-cli", appauthor=False)


def download_dir() -> Path:
    return data_dir() / "downloads"


def atomic_write(path: Path, content: bytes) -> None:
    """Owner-only on POSIX. Windows secrets use the OS credential manager, not files."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class Credential:
    token: str = field(repr=False)
    source: str = ""
    profile_id: str = ""


@dataclass(frozen=True)
class Account:
    id: str
    label: str
    team_name: str
    username: str
    storage: str
    fingerprint: str = field(repr=False)
    created_at: str = ""


class AccountStore:
    """A single active ID in an atomic index; secrets are stored separately."""

    def __init__(self, root: Path | None = None, backend=None):
        self.root = root if root is not None else config_dir()
        self.index = self.root / "accounts.json"
        self.backend = backend

    def _keyring(self):
        if self.backend is not None:
            return self.backend if self.backend.priority > 0 else None
        try:
            import keyring

            backend = keyring.get_keyring()
            return backend if backend.priority > 0 else None
        except Exception:
            return None

    def _read(self) -> dict:
        if not self.index.exists():
            return {"version": 1, "active": None, "accounts": []}
        try:
            if self.index.stat().st_size > 256 * 1024:
                raise ValueError
            data = json.loads(self.index.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("accounts"), list):
                raise ValueError
            accounts = [Account(**item) for item in data["accounts"]]
            ids = [a.id for a in accounts]
            for account in accounts:
                if (
                    str(uuid.UUID(account.id)) != account.id
                    or account.storage not in ("keyring", "file")
                    or not re.fullmatch(r"[a-f0-9]{64}", account.fingerprint)
                    or any(
                        not isinstance(value, str) or len(value) > limit
                        for value, limit in (
                            (account.label, 64),
                            (account.team_name, 100),
                            (account.username, 100),
                            (account.created_at, 64),
                        )
                    )
                ):
                    raise ValueError
            if len(ids) != len(set(ids)) or len(ids) > 20 or data.get("active") not in [None, *ids]:
                raise ValueError
            return data
        except (ValueError, TypeError, AttributeError, OSError, RecursionError) as error:
            raise ValueError(
                "Account index is unreadable. Restore accounts.json from a backup before changing keys."
            ) from error

    def _write(self, data: dict) -> None:
        atomic_write(self.index, (json.dumps(data, indent=2) + "\n").encode())

    def _lock(self) -> FileLock:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return FileLock(self.root / "accounts.lock", timeout=10)

    def accounts(self) -> list[Account]:
        return [Account(**item) for item in self._read()["accounts"]]

    @property
    def active_id(self) -> str | None:
        return self._read().get("active")

    def resolve(self, identifier: str) -> Account:
        matches = [
            a
            for a in self.accounts()
            if a.id == identifier or a.id.startswith(identifier) or a.label == identifier
        ]
        if len(matches) != 1:
            raise ValueError("Choose a unique saved account ID or label.")
        return matches[0]

    def credential(self, identifier: str) -> Credential:
        account = self.resolve(identifier)
        if account.storage == "file":
            if sys.platform == "win32":
                raise ValueError(
                    "Delete this saved profile, then add the key in Windows Credential Manager."
                )
            try:
                path = self.root / "secrets" / f"{account.id}.key"
                token = path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ValueError(
                    "Saved key is missing or unreadable. Delete this profile and add the key again."
                ) from error
        else:
            backend = self._keyring()
            if backend is None:
                raise ValueError("Unlock or enable your OS keyring to use this account.")
            try:
                token = backend.get_password(SERVICE, account.id)
            except Exception as error:
                raise ValueError(
                    "Your OS keyring could not read this key. Unlock it and try again."
                ) from error
        if (
            not isinstance(token, str)
            or not KEY_PATTERN.fullmatch(token)
            or hashlib.sha256(token.encode()).hexdigest() != account.fingerprint
        ):
            raise ValueError(
                "Saved key is missing or invalid. Delete the profile and add the key again."
            )
        return Credential(token, f"{account.label} ({account.storage})", account.id)

    def active_credential(self) -> Credential | None:
        ident = self.active_id
        return self.credential(ident) if ident else None

    def add(self, token: str, label: str, who: dict, *, activate: bool = False) -> Account:
        """Caller verifies GET /me first. A duplicate key reuses its saved profile."""
        if (
            not isinstance(who, dict)
            or not isinstance(who.get("team"), dict)
            or not isinstance(who.get("user") or {}, dict)
        ):
            raise ValueError("The API did not return valid account information. No key was saved.")
        if not KEY_PATTERN.fullmatch(token):
            raise ValueError("Use an API key from your team page, starting with bc_.")

        def label_text(value, limit):
            return redact(" ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).split()))[:limit]

        team_name = label_text((who.get("team") or {}).get("name", "Your team"), 100)
        username = label_text((who.get("user") or {}).get("username", ""), 100)
        label = label_text(label or team_name, 64)
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        with self._lock():
            data = self._read()
            duplicate = next((a for a in data["accounts"] if a["fingerprint"] == fingerprint), None)
            if duplicate:
                # Re-saving repairs a missing secret without creating a second profile.
                account = Account(
                    **{**duplicate, "label": label, "team_name": team_name, "username": username}
                )
                self._save_secret(account, token)
                data["accounts"] = [
                    asdict(account) if a["id"] == account.id else a for a in data["accounts"]
                ]
            else:
                if len(data["accounts"]) >= 20:
                    raise ValueError("Maximum 20 saved keys. Delete an unused profile first.")
                backend = self._keyring()
                storage = "keyring" if backend is not None else "file"
                if storage == "file" and sys.platform == "win32":
                    raise ValueError(
                        "Windows Credential Manager is unavailable. Enable it before saving a key."
                    )
                account = Account(
                    str(uuid.uuid4()),
                    label,
                    team_name,
                    username,
                    storage,
                    fingerprint,
                    datetime.now(UTC).isoformat(),
                )
                self._save_secret(account, token)
                data["accounts"].append(asdict(account))
            if activate:
                data["active"] = account.id
            try:
                self._write(data)
            except OSError:
                if not duplicate:
                    self._delete_secret(account)
                raise
        return account

    def _save_secret(self, account: Account, token: str) -> None:
        if account.storage == "file":
            if sys.platform == "win32":
                raise ValueError(
                    "Delete this saved profile, then add the key in Windows Credential Manager."
                )
            atomic_write(self.root / "secrets" / f"{account.id}.key", token.encode())
            return
        backend = self._keyring()
        if backend is None:
            raise ValueError("Unlock or enable your OS keyring before saving this key.")
        try:
            backend.set_password(SERVICE, account.id, token)
        except Exception as error:
            raise ValueError(
                "Your OS keyring could not save this key. Unlock it and try again."
            ) from error

    def activate(self, identifier: str) -> Credential:
        with self._lock():
            credential = self.credential(identifier)
            data = self._read()
            data["active"] = credential.profile_id
            self._write(data)
            return credential

    def disconnect(self) -> None:
        with self._lock():
            data = self._read()
            data["active"] = None
            self._write(data)

    def _delete_secret(self, account: Account) -> None:
        if account.storage == "file":
            (self.root / "secrets" / f"{account.id}.key").unlink(missing_ok=True)
            return
        backend = self._keyring()
        if backend is None:
            raise ValueError("Unlock your OS keyring before deleting the saved key.")
        try:
            backend.delete_password(SERVICE, account.id)
        except Exception as error:
            from keyring.errors import PasswordDeleteError

            if not isinstance(error, PasswordDeleteError):
                raise ValueError(
                    "Your OS keyring could not delete the saved key. Unlock it and try again."
                ) from error

    def delete(self, identifier: str) -> None:
        with self._lock():
            account = self.resolve(identifier)
            data = self._read()
            if data["active"] == account.id:
                # Persist disconnect before deleting the secret, even if final cleanup fails.
                data["active"] = None
                self._write(data)
            self._delete_secret(account)
            data["accounts"] = [a for a in data["accounts"] if a["id"] != account.id]
            self._write(data)


def importable_credential() -> Credential | None:
    """Offered explicitly in setup, never automatically connected or used after logout."""
    for name in ("BATTLECODE_API_KEY", "UNSWBC_KEY"):
        token = os.environ.get(name, "").strip()
        if KEY_PATTERN.fullmatch(token):
            return Credential(token, name)
    for path in (config_dir() / "credentials.json", Path.home() / ".unswbc" / "keys.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get(SERVER) if isinstance(data, dict) else None
            if isinstance(token, str) and KEY_PATTERN.fullmatch(token):
                return Credential(token, str(path))
        except (OSError, ValueError):
            pass
    return None


def load_credential() -> Credential | None:
    return AccountStore().active_credential()


def redact(text: str, token: str = "") -> str:
    if token:
        text = text.replace(token, "[redacted]")
    return re.sub(r"bc_[A-Za-z0-9_-]+", "[redacted]", text)
