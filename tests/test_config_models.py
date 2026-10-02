import json
import stat

import pytest

from battlecode_cli import config
from battlecode_cli.models import active_bot, battle_row, date_label, rows, winrate


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.delenv("BATTLECODE_API_KEY", raising=False)
    monkeypatch.delenv("UNSWBC_KEY", raising=False)
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    return tmp_path


def test_credential_priority_and_private_permissions(isolated_config, monkeypatch):
    root = isolated_config
    toolkit = root / ".unswbc"
    toolkit.mkdir()
    (toolkit / "keys.json").write_text(json.dumps({config.SERVER: "bc_toolkit_fixture"}))
    assert config.load_credential().token == "bc_toolkit_fixture"
    config.save_credential("bc_app_fixture")
    assert config.load_credential().token == "bc_app_fixture"
    mode = stat.S_IMODE((root / "config" / "credentials.json").stat().st_mode)
    assert mode == 0o600
    monkeypatch.setenv("UNSWBC_KEY", "bc_unswbc_env_fixture")
    assert config.load_credential().token == "bc_unswbc_env_fixture"
    monkeypatch.setenv("BATTLECODE_API_KEY", "bc_dashboard_env_fixture")
    assert config.load_credential().token == "bc_dashboard_env_fixture"


def test_clear_leaves_toolkit_file_alone(isolated_config):
    config.save_credential("bc_app_fixture")
    config.clear_credential()
    assert config.load_credential() is None


def test_bad_credential_file(isolated_config):
    path = isolated_config / "config"
    path.mkdir()
    (path / "credentials.json").write_text("broken json")
    assert config.load_credential() is None
    with pytest.raises(ValueError):
        config.save_credential("not-a-key")


def test_credentials_are_not_in_repr():
    assert "bc_fixture" not in repr(config.Credential("bc_fixture", "test"))
    assert config.redact("token bc_fixture here") == "token [redacted] here"


def test_winrate_includes_draws():
    assert winrate({"wins": 5, "draws": 2, "losses": 3}) == "50.0%"
    assert winrate({}) == "n/a"
    assert winrate({"record": {"wins": 1, "draws": 0, "losses": 1}}) == "50.0%"


def test_wrapped_lists():
    assert rows({"submissions": [{"id": 1}]}, "submissions") == [{"id": 1}]
    assert rows(None) == []
    assert active_bot([{"status": "idle"}, {"status": "active", "id": 2}])["id"] == 2


def test_battle_orientation_and_lowercase_winner():
    value = {
        "match": {"id": 12, "teamAId": 1, "teamBId": 2, "winner": "b", "ranked": True},
        "teamAName": "Opponent",
        "teamBName": "Us",
    }
    battle = battle_row(value, 2)
    assert battle["opponent"] == "Opponent"
    assert battle["result"] == "WIN"
    assert battle["mode"] == "Ranked"
    assert battle_row(value, 1)["result"] == "LOSS"


def test_recent_battle_summary():
    battle = battle_row(
        {
            "id": 2,
            "opponent": "Team",
            "outcome": "win",
            "results": ["win", "draw", "loss"],
            "eloChange": 4,
        }
    )
    assert battle["score"] == "W D L"
    assert battle["change"] == "+4"
    assert date_label(None) == "n/a"
