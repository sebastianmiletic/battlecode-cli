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


WHO = {"team": {"name": "Fixture team", "id": 1}, "user": {"username": "tester"}}


def test_only_explicit_active_profile_is_loaded(isolated_config, monkeypatch):
    toolkit = isolated_config / ".unswbc"
    toolkit.mkdir()
    (toolkit / "keys.json").write_text(json.dumps({config.SERVER: "bc_toolkit_fixture"}))
    assert config.load_credential() is None
    assert config.importable_credential().token == "bc_toolkit_fixture"
    store = config.AccountStore()
    account = store.add("bc_app_fixture", "Main", WHO, activate=True)
    assert config.load_credential().token == "bc_app_fixture"
    assert config.load_credential().profile_id == account.id
    monkeypatch.setenv("UNSWBC_KEY", "bc_unswbc_env_fixture")
    monkeypatch.setenv("BATTLECODE_API_KEY", "bc_dashboard_env_fixture")
    assert config.importable_credential().token == "bc_dashboard_env_fixture"
    assert config.load_credential().token == "bc_app_fixture"
    store.disconnect()
    assert config.load_credential() is None
    assert config.importable_credential().token == "bc_dashboard_env_fixture"


def test_profiles_keyring_deduplication_switch_delete(private_test_environment):
    store = config.AccountStore()
    first = store.add("bc_first_fixture", "One", WHO, activate=True)
    second = store.add("bc_second_fixture", "Two", WHO)
    assert store.active_id == first.id
    assert len(store.accounts()) == 2
    assert "bc_first_fixture" not in store.index.read_text()
    assert first.storage == "keyring"
    duplicate = store.add("bc_first_fixture", "Renamed", WHO)
    assert duplicate.id == first.id
    assert len(store.accounts()) == 2
    store.activate(second.id)
    assert store.active_credential().token == "bc_second_fixture"
    store.delete(second.id)
    assert store.active_id is None
    assert store.active_credential() is None
    assert len(store.accounts()) == 1
    assert len(private_test_environment.values) == 1
    store.delete(first.id)
    assert private_test_environment.values == {}


def test_corrupt_secret_is_not_used(private_test_environment):
    store = config.AccountStore()
    account = store.add("bc_correct_fixture", "Main", WHO, activate=True)
    private_test_environment.values[(config.SERVICE, account.id)] = "bc_changed_fixture"
    with pytest.raises(ValueError, match="missing or invalid"):
        store.active_credential()


def test_private_file_fallback(isolated_config, monkeypatch):
    import os
    import sys

    if os.name != "posix":
        pytest.skip("POSIX fallback; Windows always requires its OS keyring")
    monkeypatch.setattr(config.AccountStore, "_keyring", staticmethod(lambda: None))
    store = config.AccountStore()
    account = store.add("bc_file_fixture", "Private file", WHO, activate=True)
    assert account.storage == "file"
    secret = store.root / "secrets" / f"{account.id}.key"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.index.stat().st_mode) == 0o600
    assert store.active_credential().token == "bc_file_fixture"
    store.delete(account.id)
    assert not secret.exists()
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError, match="Credential Manager"):
        store.add("bc_windows_fixture", "No plaintext", WHO)


def test_clear_leaves_toolkit_file_alone(isolated_config):
    toolkit = isolated_config / ".unswbc"
    toolkit.mkdir()
    path = toolkit / "keys.json"
    path.write_text(json.dumps({config.SERVER: "bc_toolkit_fixture"}))
    store = config.AccountStore()
    account = store.add("bc_app_fixture", "Main", WHO, activate=True)
    store.delete(account.id)
    assert config.load_credential() is None
    assert path.exists()


def test_bad_credential_file(isolated_config):
    path = isolated_config / "config"
    path.mkdir()
    (path / "credentials.json").write_text("broken json")
    assert config.load_credential() is None
    assert config.importable_credential() is None
    with pytest.raises(ValueError):
        config.AccountStore().add("not-a-key", "Bad", WHO)
    (path / "accounts.json").write_text("broken json")
    with pytest.raises(ValueError, match="index is unreadable"):
        config.AccountStore().accounts()


def test_profile_labels_are_plain_redacted_and_ids_cannot_traverse():
    store = config.AccountStore()
    account = store.add("bc_label_fixture", "bc_must_hide\x1b[31m", WHO)
    assert "bc_must_hide" not in account.label
    assert "\x1b" not in account.label
    with pytest.raises(ValueError):
        store.resolve("../outside")


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


def test_windows_never_saves_a_plaintext_key(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config.AccountStore, "_keyring", staticmethod(lambda: None))
    store = config.AccountStore()
    with pytest.raises(ValueError, match="Credential Manager"):
        store.add("bc_windows_fixture", "Test", WHO)
    assert store.accounts() == []
    assert not (store.root / "secrets").exists()


def test_invalid_me_response_is_not_saved():
    with pytest.raises(ValueError, match="account information"):
        config.AccountStore().add("bc_fixture", "Test", {"team": []})
