import io
import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from rich.console import Console

from battlecode_cli import cli
from battlecode_cli.api import BattlecodeAPI
from battlecode_cli.config import AccountStore


@pytest.fixture
def cli_server(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        token = request.headers["authorization"]
        if "rejected" in token:
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(
            200, json={"team": {"name": "Fixture team"}, "user": {"username": "tester"}}
        )

    monkeypatch.setattr(
        cli,
        "BattlecodeAPI",
        lambda credential: BattlecodeAPI(credential, httpx.MockTransport(handler)),
    )
    return requests


def arguments(*values):
    return cli.parser().parse_args(["auth", *values])


def output():
    buffer = io.StringIO()
    return Console(file=buffer, color_system=None, width=120), buffer


async def test_hidden_add_list_use_disconnect_and_delete(cli_server, monkeypatch):
    console, buffer = output()
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "bc_cli_fixture")
    assert await cli.auth_command(arguments("add", "--name", "Main", "--save-only"), console) == 0
    store = AccountStore()
    account = store.accounts()[0]
    assert store.active_id is None
    assert await cli.auth_command(arguments("list"), console) == 0
    assert await cli.auth_command(arguments("use", account.id[:8]), console) == 0
    assert store.active_id == account.id
    assert await cli.auth_command(arguments("status"), console) == 0
    assert await cli.auth_command(arguments("disconnect"), console) == 0
    assert store.active_id is None
    assert await cli.auth_command(arguments("delete", account.id[:8], "--yes"), console) == 0
    assert store.accounts() == []
    assert "bc_cli_fixture" not in buffer.getvalue()
    assert all(request.method == "GET" for request in cli_server)


async def test_rejected_key_is_never_saved(cli_server, monkeypatch):
    console, buffer = output()
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "bc_rejected_fixture")
    assert await cli.auth_command(arguments("set"), console) == 1
    assert AccountStore().accounts() == []
    assert "bc_rejected_fixture" not in buffer.getvalue()


async def test_stdin_and_explicit_environment_import(cli_server, monkeypatch):
    console, buffer = output()
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("bc_stdin_fixture\n"))
    assert await cli.auth_command(arguments("add", "--stdin"), console) == 0
    monkeypatch.setenv("BATTLECODE_API_KEY", "bc_env_fixture")
    first = AccountStore().active_id
    assert await cli.auth_command(arguments("import", "--save-only"), console) == 0
    assert AccountStore().active_id == first
    assert len(AccountStore().accounts()) == 2
    assert "bc_env_fixture" not in buffer.getvalue()


async def test_deletion_requires_confirmation_noninteractively(cli_server, monkeypatch):
    store = AccountStore()
    account = store.add("bc_delete_fixture", "Main", {"team": {"name": "Test"}}, activate=True)
    console, buffer = output()
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    assert await cli.auth_command(arguments("delete", account.id[:8]), console) == 1
    assert len(store.accounts()) == 1
    assert store.active_id == account.id
    assert "--yes" in buffer.getvalue()
    assert cli_server == []


def test_replay_info_without_authentication(tmp_path, monkeypatch, capsys):
    replay = tmp_path / "space in filename.replay"
    replay.write_bytes(files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes())
    monkeypatch.setattr(cli.sys, "argv", ["battlecode-cli", "replay", str(replay), "--info"])
    cli.main()
    assert '"frames": 7' in capsys.readouterr().out


def test_noninteractive_dashboard_has_useful_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["battlecode-cli"])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    assert "interactive terminal" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "posix", reason="Windows installer is exercised in GitHub Actions")
@pytest.mark.parametrize("local", [True, False])
def test_shell_installer_quotes_paths_and_can_run_from_github(tmp_path, local):
    root = tmp_path / "project with spaces"
    root.mkdir()
    installer = Path(__file__).resolve().parents[1] / "install.sh"
    shutil.copyfile(installer, root / "install.sh")
    if local:
        module = root / "src" / "battlecode_cli"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
    bin_dir = tmp_path / "bin with spaces"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" >> "$TRACE"\nif [ "$1 $2 $3" = "tool dir --bin" ]; then printf "%s\\n" "$TEST_BIN"; fi\n'
    )
    uv.chmod(0o755)
    command = bin_dir / "battlecode-cli"
    command.write_text('#!/bin/sh\nprintf "%s\\n" "battlecode-cli 0.4.1"\n')
    command.chmod(0o755)
    trace = tmp_path / "trace"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
        "TRACE": str(trace),
        "TEST_BIN": str(bin_dir),
    }
    command_line = ["sh", "./install.sh"] if local else ["sh", "-c", "sh < install.sh"]
    result = subprocess.run(
        command_line, cwd=root, env=env, capture_output=True, text=True, check=True
    )
    lines = trace.read_text().splitlines()
    expected = (
        str(root)
        if local
        else "https://github.com/sebastianmiletic/battlecode-cli/archive/refs/tags/v0.4.1.tar.gz"
    )
    assert expected in lines
    assert "--python" in lines
    assert "3.13" in lines
    assert "battlecode-cli 0.4.1" in result.stdout
