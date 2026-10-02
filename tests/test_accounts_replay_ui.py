from importlib.resources import files
from types import SimpleNamespace

import httpx
import pytest
from textual.widgets import ContentSwitcher, DataTable, Input, Select, Static

from battlecode_cli import app as app_module
from battlecode_cli.api import BattlecodeAPI
from battlecode_cli.app import BattlecodeApp
from battlecode_cli.config import AccountStore, Credential
from battlecode_cli.dialogs import Confirm, FilePicker
from battlecode_cli.replay_viewer import ReplayBoard, ReplayViewer, Timeline
from battlecode_cli.replays import ReplayLibrary


@pytest.fixture
def account_server(monkeypatch):
    calls = []

    def handler(request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        calls.append((request.method, request.url.path, token))
        assert request.method == "GET", "No actual account-changing API calls in these tests"
        if token == "bc_rejected_fixture":
            return httpx.Response(401, json={"error": "rejected"})
        team = {
            "id": 1 if token == "bc_first_fixture" else 2,
            "name": "Alpha team" if token == "bc_first_fixture" else "Beta team",
            "elo": 1500,
        }
        path = request.url.path.removeprefix("/api/v1")
        if path == "/me":
            return httpx.Response(200, json={"team": team, "user": {"username": "tester"}})
        if path == "/team":
            return httpx.Response(200, json={"team": team, "rank": 2})
        if path.endswith("/replay"):
            return httpx.Response(
                200,
                content=files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes(),
            )
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        app_module, "BattlecodeAPI", lambda credential: BattlecodeAPI(credential, transport)
    )
    return calls


async def visible_click(app, pilot, selector):
    # Let deferred focus/scroll requests settle, then click inside the button, not its edge.
    await pilot.pause(0.1)
    target = app.query_one(selector)
    target.scroll_visible(animate=False, immediate=True)
    await pilot.pause(0.2)
    assert await pilot.click(target, offset=(1, 1))
    await pilot.pause(0.3)


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_first_run_key_prompt_connect_and_persistent_disconnect(
    size, account_server, monkeypatch
):
    monkeypatch.setenv("BATTLECODE_API_KEY", "bc_environment_fixture")
    app = BattlecodeApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.2)
        assert app.query_one("#pages", ContentSwitcher).current == "settings"
        assert app.focused is app.query_one("#api-key", Input)
        assert app.query_one("#api-key").region.bottom <= app.query_one("#pages").region.bottom
        assert account_server == []  # no automatic environment/toolkit login
        app.query_one("#api-key", Input).value = "bc_first_fixture"
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert app.accounts.active_id == app.api.credential.profile_id
        assert app.team["team"]["name"] == "Alpha team"
        assert app.query_one("#api-key", Input).value == ""
        assert "bc_first_fixture" not in app.export_screenshot()
        await visible_click(app, pilot, "#disconnect-account")
        assert app.api.credential is None
        assert app.accounts.active_id is None
        assert len(app.accounts.accounts()) == 1
        assert app.team == {}
        count = len(account_server)
        app.poll()
        await pilot.pause(0.2)
        assert len(account_server) == count
    restarted = BattlecodeApp()
    assert restarted.api.credential is None
    await restarted.api.close()


async def test_save_only_switch_cancel_accept_and_delete(account_server):
    store = AccountStore()
    one = store.add("bc_first_fixture", "First", {"team": {"name": "Alpha team"}}, activate=True)
    app = BattlecodeApp(accounts=store)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.3)
        app.action_view("settings")
        app.query_one("#api-key", Input).value = "bc_second_fixture"
        app.query_one("#account-label", Input).value = "Second"
        await visible_click(app, pilot, "#save-key")
        assert len(store.accounts()) == 2
        assert store.active_id == one.id
        two = next(a for a in store.accounts() if a.label == "Second")
        app.query_one("#accounts-table", DataTable).move_cursor(row=1)
        await pilot.pause(0.1)
        await visible_click(app, pilot, "#use-account")
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert store.active_id == one.id
        await visible_click(app, pilot, "#use-account")
        await pilot.click("#accept-confirm")
        await pilot.pause(0.4)
        assert store.active_id == two.id
        assert app.api.credential.profile_id == two.id
        assert app.team["team"]["name"] == "Beta team"
        assert app.bot_id is None
        assert app.game_id is None
        await visible_click(app, pilot, "#delete-account")
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert len(store.accounts()) == 2
        await visible_click(app, pilot, "#delete-account")
        await pilot.click("#accept-confirm")
        await pilot.pause(0.4)
        assert store.active_id is None
        assert app.api.credential is None
        assert len(store.accounts()) == 1
        assert store.accounts()[0].id == one.id
        assert app.team == {}
        assert all(method == "GET" for method, _, _ in account_server)


async def test_rejected_new_key_does_not_replace_active_account(account_server):
    store = AccountStore()
    one = store.add("bc_first_fixture", "First", {"team": {"name": "Alpha"}}, activate=True)
    app = BattlecodeApp(accounts=store)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.3)
        app.action_view("settings")
        app.query_one("#api-key", Input).value = "bc_rejected_fixture"
        await visible_click(app, pilot, "#connect-key")
        assert store.active_id == one.id
        assert len(store.accounts()) == 1
        assert app.api.credential.profile_id == one.id
        assert "bc_rejected_fixture" not in str(app.query_one("#settings-status", Static).render())
        assert app.query_one("#api-key", Input).value == ""


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_mouse_navigation_replay_playback_seek_inspection_and_back(size, tmp_path):
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.2)
        # Real mouse navigation, not just calling action_view().
        nav = app.query_one("#nav")
        assert await pilot.click(nav, offset=(4, 7))
        await pilot.pause(0.2)
        assert app.query_one("#pages", ContentSwitcher).current == "replays"
        await visible_click(app, pilot, "#sample-replay")
        assert isinstance(app.screen, ReplayViewer)
        viewer = app.screen
        assert viewer.index == 0
        assert viewer.detailed == (size[0] >= 130)
        for ident in ("replay-mode", "replay-speed"):
            select = viewer.query_one(f"#{ident}", Select)
            label = select.query_one("#label", Static)
            assert label.region.y >= select.region.y
            assert label.region.bottom <= select.region.bottom
            assert label.render().plain
        assert "frames/s" in viewer.query_one("#replay-speed #label", Static).render().plain
        await pilot.click("#replay-next")
        await pilot.pause(0.1)
        assert viewer.index == 1
        await pilot.press("left")
        await pilot.pause(0.1)
        assert viewer.index == 0
        timeline = viewer.query_one(Timeline)
        await pilot.click(timeline, offset=(timeline.size.width - 1, 0))
        await pilot.pause(0.1)
        assert viewer.index == len(viewer.replay.frames) - 1
        await pilot.click("#replay-play")
        await pilot.pause(0.4)
        assert viewer.playing
        assert viewer.index > 0
        await pilot.click("#replay-play")
        await pilot.pause(0.1)
        assert not viewer.playing
        viewer.action_seek(0)
        await pilot.pause(0.1)
        board = viewer.query_one(ReplayBoard)
        dragon = viewer.replay.frames[0].dragons[0]
        x, y = dragon.body[0]
        px, py = (2 * x + 1, 2 * y + 1) if board.detailed else (x, y)
        await pilot.click(board, offset=(px, py))
        await pilot.pause(0.1)
        assert (
            "dragon #0" in viewer.query_one("#replay-inspector", Static).render().plain
            or "queen #0" in viewer.query_one("#replay-inspector", Static).render().plain
        )
        # Background dashboard refresh must work while a replay screen is open.
        app.poll()
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, ReplayViewer)
        assert app.query_one("#pages", ContentSwitcher).current == "replays"


async def test_import_picker_library_and_confirmed_removal(tmp_path):
    source = tmp_path / "game with spaces.replay"
    source.write_bytes(files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes())
    library = ReplayLibrary(tmp_path / "library")
    app = BattlecodeApp(library=library)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.1)
        await visible_click(app, pilot, "#continue-offline")
        app.query_one("#replay-path", Input).value = str(source)
        await pilot.click("#browse-replay")
        await pilot.pause(0.2)
        assert isinstance(app.screen, FilePicker)
        await pilot.click("#choose-file")
        await pilot.pause(0.1)
        await pilot.click("#import-replay")
        await pilot.pause(0.4)
        assert isinstance(app.screen, ReplayViewer)
        assert len(library.entries()) == 1
        await pilot.press("escape")
        await pilot.pause(0.1)
        await visible_click(app, pilot, "#remove-replay")
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert len(library.entries()) == 1
        await visible_click(app, pilot, "#remove-replay")
        await pilot.click("#accept-confirm")
        await pilot.pause(0.2)
        assert library.entries() == []
        assert source.exists()
        assert app.query_one("#replays-table", DataTable).row_count == 0


async def test_game_replay_download_opens_built_in_viewer(account_server, tmp_path):
    api = app_module.BattlecodeAPI(Credential("bc_first_fixture"))
    app = BattlecodeApp(api=api, library=ReplayLibrary(tmp_path / "library"))
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.3)
        app.game_id = 7200
        app.download_replay(open_after=True)
        await pilot.pause(0.4)
        assert isinstance(app.screen, ReplayViewer)
        assert len(app.library.entries()) == 1
        assert ("GET", "/api/v1/battles/7200/replay", "bc_first_fixture") in account_server


async def test_external_disconnect_clears_cached_account_without_fallback(
    account_server, monkeypatch
):
    store = AccountStore()
    store.add("bc_first_fixture", "First", {"team": {"name": "Alpha"}}, activate=True)
    monkeypatch.setenv("BATTLECODE_API_KEY", "bc_environment_fixture")
    app = BattlecodeApp(accounts=store)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.3)
        count = len(account_server)
        store.disconnect()  # Another terminal runs auth disconnect.
        app.poll()
        await pilot.pause(0.3)
        assert app.api.credential is None
        assert app.team == {}
        assert len(account_server) == count
        assert app.query_one("#pages", ContentSwitcher).current == "settings"


async def test_external_account_change_invalidates_pending_upload(account_server, tmp_path):
    store = AccountStore()
    store.add("bc_first_fixture", "First", {"team": {"name": "Alpha"}}, activate=True)
    second = store.add("bc_second_fixture", "Second", {"team": {"name": "Beta"}})
    project = tmp_path / "bot"
    project.mkdir()
    (project / "bot.toml").write_text('[project]\nlanguage="python"\ninclude=["main.py"]\n')
    (project / "main.py").write_text("raise RuntimeError('never execute')")
    app = BattlecodeApp(accounts=store)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.3)
        app.action_view("upload")
        app.query_one("#upload-path", Input).value = str(project)
        app.query_one("#upload-name", Input).value = "Fixture"
        await visible_click(app, pilot, "#review-upload")
        assert isinstance(app.screen, Confirm)
        store.activate(second.id)  # Another terminal explicitly switches account.
        await pilot.click("#accept-confirm")
        await pilot.pause(0.4)
        assert all(method == "GET" for method, _, _ in account_server)
        assert store.active_id == second.id
        assert app.api.credential is None
        assert app.team == {}


async def test_late_table_events_ignore_unmounted_sibling_controls():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.3)
        await app.query_one("#use-account").remove()
        await app.query_one("#watch-replay").remove()
        event = SimpleNamespace(row_key=SimpleNamespace(value="ignored-late-event"))
        app.account_highlight(event)
        app.replay_highlight(event)
        assert app.account_id is None
        assert app.local_replay_id is None
