import io
import stat
import zipfile

import pytest

from battlecode_cli.bots import MAX_ZIP_BYTES, prepare_bot

TOML = b'[project]\nlanguage = "cpp"\ninclude = ["main.cpp", "*.hpp"]\n'


def archive(path, extras=None, toml=TOML):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        if toml is not None:
            output.writestr("bot.toml", toml)
        output.writestr("main.cpp", "int main() {}")
        for name, content in (extras or {}).items():
            output.writestr(name, content)
    return path


def test_valid_archive(tmp_path):
    bot = prepare_bot(str(archive(tmp_path / "bot.zip")))
    assert bot.language == "cpp"
    assert "bot.toml" in bot.files
    assert len(bot.digest) == 64
    assert "blob=" not in repr(bot)


def test_folder_obeys_include_and_excludes_build_output(tmp_path):
    (tmp_path / "bot.toml").write_bytes(TOML)
    (tmp_path / "main.cpp").write_text("int main() {}")
    (tmp_path / "core.hpp").write_text("#pragma once")
    (tmp_path / "notes.txt").write_text("not a source")
    (tmp_path / ".unswbc-build").mkdir()
    (tmp_path / ".unswbc-build" / "main.cpp").write_text("old build")
    bot = prepare_bot(str(tmp_path))
    assert set(bot.files) == {"bot.toml", "main.cpp", "core.hpp"}
    assert zipfile.ZipFile(io.BytesIO(bot.blob)).read("main.cpp") == b"int main() {}"


@pytest.mark.parametrize(
    "name",
    [
        "../outside.cpp",
        "/outside.cpp",
        "folder\\main.cpp",
        "C:/outside.cpp",
        "main.cpp:secret",
        ".ENV",
        "Credentials.json",
        ".env",
        ".env.local",
        "keys.json",
        ".git/config",
    ],
)
def test_unsafe_archive_entries(tmp_path, name):
    # Construct the raw spelling: Windows' zipfile writer otherwise converts '\\' to '/'.
    path = archive(tmp_path / "bot.zip", {name.replace("\\", "/"): "secret"})
    if "\\" in name:
        path.write_bytes(path.read_bytes().replace(name.replace("\\", "/").encode(), name.encode()))
    with pytest.raises(ValueError, match="Unsafe or sensitive"):
        prepare_bot(str(path))


def test_symlink_archive_is_refused(tmp_path):
    path = archive(tmp_path / "bot.zip")
    with zipfile.ZipFile(path, "a") as output:
        info = zipfile.ZipInfo("link.hpp")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, "elsewhere")
    with pytest.raises(ValueError, match="Symlinks"):
        prepare_bot(str(path))


def test_folder_symlink_is_refused(tmp_path):
    (tmp_path / "bot.toml").write_bytes(TOML)
    (tmp_path / "original.txt").write_text("source")
    try:
        (tmp_path / "main.cpp").symlink_to(tmp_path / "original.txt")
    except OSError:
        pytest.skip(
            "Creating symlinks requires Developer Mode or administrator permissions on Windows"
        )
    with pytest.raises(ValueError, match="symlinks"):
        prepare_bot(str(tmp_path))


def test_sensitive_glob_is_refused(tmp_path):
    (tmp_path / "bot.toml").write_bytes(b'[project]\nlanguage="python"\ninclude=["*"]\n')
    (tmp_path / ".env").write_text("TOKEN=secret")
    with pytest.raises(ValueError, match="sensitive"):
        prepare_bot(str(tmp_path))


def test_missing_root_botfile(tmp_path):
    with pytest.raises(ValueError, match="at its root"):
        prepare_bot(str(archive(tmp_path / "bot.zip", {"bot/bot.toml": TOML}, toml=None)))


def test_no_matching_sources(tmp_path):
    (tmp_path / "bot.toml").write_bytes(TOML)
    with pytest.raises(ValueError, match="No source"):
        prepare_bot(str(tmp_path))


def test_oversize_zip(tmp_path):
    path = tmp_path / "bot.zip"
    path.write_bytes(b"x" * (MAX_ZIP_BYTES + 1))
    with pytest.raises(ValueError, match="4 MB"):
        prepare_bot(str(path))


def test_corrupt_zip(tmp_path):
    path = tmp_path / "bot.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="corrupt"):
        prepare_bot(str(path))


def test_invalid_toml(tmp_path):
    with pytest.raises(ValueError, match="Invalid bot.toml"):
        prepare_bot(str(archive(tmp_path / "bot.zip", toml=b"not toml")))


def test_duplicate_names(tmp_path):
    path = archive(tmp_path / "bot.zip")
    with pytest.warns(UserWarning), zipfile.ZipFile(path, "a") as output:
        output.writestr("main.cpp", "duplicate")
    with pytest.raises(ValueError, match="duplicate"):
        prepare_bot(str(path))
