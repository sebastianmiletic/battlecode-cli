import httpx
import keyring
import pytest

from battlecode_cli import arena, config, history, replays


class MemoryKeyring:
    priority = 1

    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


@pytest.fixture(autouse=True)
def private_test_environment(tmp_path, monkeypatch):
    """Never touch the developer's actual keys, keychain or replay library."""
    monkeypatch.delenv("BATTLECODE_API_KEY", raising=False)
    monkeypatch.delenv("UNSWBC_KEY", raising=False)
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    for module in (arena, history, replays):
        monkeypatch.setattr(module, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    backend = MemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    async def forbid_network(*args, **kwargs):
        raise AssertionError("Tests must use mocked HTTP, never the real Battlecode server.")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_network)
    return backend
