"""Validate upload archives or package a bot.toml project without running its code."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import stat
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_ZIP_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000
LANGUAGES = {"py": "python", "python": "python", "cpp": "cpp", "c++": "cpp", "cxx": "cpp", "c": "c"}
SENSITIVE = {
    ".git",
    ".env",
    ".unswbc",
    ".ssh",
    ".aws",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "auth.json",
    "credentials.json",
    "keys.json",
}


@dataclass(frozen=True)
class PreparedBot:
    path: Path
    language: str
    files: tuple[str, ...]
    digest: str
    blob: bytes = field(repr=False)


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _sensitive(name: str) -> bool:
    return any(part in SENSITIVE or part.startswith(".env.") for part in PurePosixPath(name).parts)


def _project(raw: bytes) -> tuple[str, list[str], str]:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
        project = data.get("project", {})
        language = LANGUAGES.get(str(project.get("language", "")).lower())
        include = project.get("include")
        if (
            not language
            or not isinstance(include, list)
            or not all(isinstance(p, str) for p in include)
        ):
            raise ValueError("bot.toml needs a valid project.language and project.include list.")
        return language, include, str(project.get("output", ".unswbc-build"))
    except (ValueError, UnicodeError, AttributeError) as error:
        raise ValueError(f"Invalid bot.toml: {error}") from error


def _validate(blob: bytes, path: Path) -> PreparedBot:
    if len(blob) > MAX_ZIP_BYTES:
        raise ValueError("The ZIP exceeds the server's 4 MB upload limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            names = [item.filename for item in entries]
            if (
                len(entries) > MAX_FILES
                or sum(item.file_size for item in entries) > MAX_SOURCE_BYTES
            ):
                raise ValueError("Archive exceeds the source-file safety limit.")
            if len(names) != len(set(names)):
                raise ValueError("Archive contains duplicate filenames.")
            for item in archive.infolist():
                if not _safe_name(item.filename) or _sensitive(item.filename):
                    raise ValueError(f"Unsafe or sensitive archive entry: {item.filename}")
                if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise ValueError("Symlinks and encrypted archives are not supported.")
            if "bot.toml" not in names:
                raise ValueError("ZIP must contain bot.toml at its root, not inside a bot folder.")
            language, includes, _ = _project(archive.read("bot.toml"))
            if not any(
                n != "bot.toml"
                and any(
                    fnmatch.fnmatchcase(n, p) or fnmatch.fnmatchcase(PurePosixPath(n).name, p)
                    for p in includes
                )
                for n in names
            ):
                raise ValueError("No source files match project.include.")
            # Read every member once to verify CRCs before anything is uploaded.
            for item in entries:
                archive.read(item)
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError("The ZIP is corrupt or unreadable.") from error
    return PreparedBot(path, language, tuple(names), hashlib.sha256(blob).hexdigest(), blob)


def prepare_bot(value: str) -> PreparedBot:
    path = Path(value.strip().strip("\"' ")).expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() != ".zip":
            raise ValueError("Choose a .zip file or a folder containing bot.toml.")
        if path.stat().st_size > MAX_ZIP_BYTES:
            raise ValueError("The ZIP exceeds the server's 4 MB upload limit.")
        return _validate(path.read_bytes(), path)
    if not path.is_dir() or not (path / "bot.toml").is_file():
        raise ValueError("Choose a .zip file or a folder containing bot.toml.")
    if (path / "bot.toml").is_symlink():
        raise ValueError("bot.toml cannot be a symlink.")
    raw = (path / "bot.toml").read_bytes()
    _, includes, output = _project(raw)
    output_dir = (path / output).resolve()
    sources: list[Path] = []
    for file in path.rglob("*"):
        if not file.is_file() or file.name == "bot.toml":
            continue
        if output_dir == file.resolve() or output_dir in file.resolve().parents:
            continue
        relative = file.relative_to(path).as_posix()
        if not any(
            fnmatch.fnmatchcase(relative, p) or fnmatch.fnmatchcase(file.name, p) for p in includes
        ):
            continue
        if file.is_symlink() or any(
            parent.is_symlink()
            for parent in file.parents
            if parent != path and parent.is_relative_to(path)
        ):
            raise ValueError(f"Source symlinks are not allowed: {relative}")
        if _sensitive(relative):
            raise ValueError(f"project.include selects a sensitive file: {relative}")
        sources.append(file)
        if len(sources) > MAX_FILES:
            raise ValueError("Too many source files.")
    if sum(p.stat().st_size for p in sources) + len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Sources exceed the 32 MB safety limit.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bot.toml", raw)
        for file in sorted(sources):
            archive.write(file, file.relative_to(path).as_posix())
    return _validate(buffer.getvalue(), path)
