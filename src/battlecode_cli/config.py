"""Credentials stay outside the checkout and never appear in object reprs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_path, user_data_path

SERVER = "https://game.battlecode.au"
API_URL = f"{SERVER}/api/v1"
KEY_PATTERN = re.compile(r"^bc_[A-Za-z0-9_-]+$")


def config_dir() -> Path:
    return user_config_path("battlecode-cli", appauthor=False)


def download_dir() -> Path:
    return user_data_path("battlecode-cli", appauthor=False) / "downloads"


@dataclass(frozen=True)
class Credential:
    token: str = field(repr=False)
    source: str = ""


def _stored(path: Path, server: str = SERVER) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get(server) if isinstance(data, dict) else None
        return value if isinstance(value, str) and KEY_PATTERN.fullmatch(value) else None
    except (OSError, ValueError):
        return None


def load_credential() -> Credential | None:
    for name in ("BATTLECODE_API_KEY", "UNSWBC_KEY"):
        if token := os.environ.get(name, "").strip():
            return Credential(token, name)
    for path in (config_dir() / "credentials.json", Path.home() / ".unswbc" / "keys.json"):
        if token := _stored(path):
            return Credential(token, str(path))
    return None


def save_credential(token: str) -> None:
    if not KEY_PATTERN.fullmatch(token):
        raise ValueError("Use an API key from your team page, starting with bc_.")
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({SERVER: token}, handle)
            handle.write("\n")
        os.replace(temporary, directory / "credentials.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def clear_credential() -> None:
    (config_dir() / "credentials.json").unlink(missing_ok=True)


def redact(text: str, token: str = "") -> str:
    if token:
        text = text.replace(token, "[redacted]")
    return re.sub(r"bc_[A-Za-z0-9_-]+", "[redacted]", text)
