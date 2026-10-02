from copy import deepcopy

import httpx
import pytest
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Select, Static, TabbedContent

from battlecode_cli.api import APIError, BattlecodeAPI
from battlecode_cli.app import BattlecodeApp
from battlecode_cli.config import Credential
from battlecode_cli.demo import SUBMISSIONS, DemoAPI
from battlecode_cli.dialogs import Confirm, TextViewer


class MockAccount(DemoAPI):
    demo = False
    credential = Credential("bc_ui_fixture_not_a_real_key", "test")

    def __init__(self):
        self.calls = []
        self.bots = deepcopy(SUBMISSIONS)
        self.failed = False

    async def get(self, path):
        if self.failed:
            raise APIError("Server unavailable")
        if path == "/submissions":
            return deepcopy(self.bots)
        return await super().get(path)

    async def activate(self, ident):
        self.calls.append(("activate", ident))
        for bot in self.bots:
            bot["status"] = "active" if bot["id"] == ident else "idle"
        return {"ok": True}

    async def challenge(self, team_id, ranked, map_ids):
        self.calls.append(("challenge", team_id, ranked, map_ids))
        return {"ids": [123]}

    async def upload(self, name, description, language, blob):
        self.calls.append(("upload", name, description, language, blob))
        return {"version": 5, "status": "processing"}


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_dashboard_navigation_and_details(size):
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.3)
        assert app.team["team"]["name"] == "Night shift"
        overview_table = app.query_one("#overview-games", DataTable)
        assert overview_table.row_count == 6
        assert overview_table.content_region.height >= 4
        await pilot.press("2", "enter")
        await pilot.pause(0.2)
        assert isinstance(app.screen, TextViewer)
        await pilot.press("escape", "3", "enter")
        await pilot.pause(0.2)
        assert app.query_one("#game-parts", DataTable).row_count == 5
        assert app.game_id == 7200
        assert not app.query_one("#download-replay", Button).disabled
        table = app.query_one("#game-parts", DataTable)
        assert str(table.get_row_at(0)[3]) == "WIN"
        assert str(table.get_row_at(1)[3]) == "LOSS"
        for page in ("arena", "leaderboard", "settings", "overview"):
            app.action_view(page)
            await pilot.pause(0.1)
            assert app.query_one("#pages", ContentSwitcher).current == page
        for alias, page, tabs, pane in (
            ("upload", "bots", "bot-tabs", "bot-upload"),
            ("challenge", "arena", "arena-tabs", "arena-online"),
            ("replays", "games", "game-tabs", "game-local"),
        ):
            app.action_view(alias)
            await pilot.pause(0.1)
            assert app.query_one("#pages", ContentSwitcher).current == page
            assert app.query_one(f"#{tabs}", TabbedContent).active == pane
        app.action_view("overview")
        await pilot.pause(0.1)
        # Header and nav labels must actually render, not just exist in widgets.
        assert "BATTLECODE" in app.query_one("#wordmark", Static).render().plain
        nav = app.query_one("#nav")
        assert "Overview" in "".join(seg.text for seg in nav.render_line(0))


async def test_leaderboard_search_and_challenge_prefill():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("5")
        app.query_one("#ladder-search", Input).value = "atlas"
        await pilot.pause(0.2)
        assert app.query_one("#ladder-table", DataTable).row_count == 1
        await pilot.press("tab", "enter")
        await pilot.pause(0.2)
        assert app.query_one("#pages", ContentSwitcher).current == "arena"
        assert app.query_one("#arena-tabs", TabbedContent).active == "arena-online"
        assert app.query_one("#challenge-team", Input).value == "2"
        assert app.query_one("#challenge-mode", Select).value == "practice"


async def test_invalid_key_opens_settings_and_pauses_polling():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"error": "Invalid"})

    api = BattlecodeAPI(Credential("bc_rejected_fixture"), httpx.MockTransport(handler))
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.pause(0.3)
        assert app.auth_failed
        assert app.query_one("#pages", ContentSwitcher).current == "settings"
        count = len(requests)
        app.poll()
        await pilot.pause(0.1)
        assert len(requests) == count
        assert "bc_rejected_fixture" not in str(
            app.query_one("#connection-summary", Static).render()
        )


async def test_activation_requires_confirmation_and_cancel_does_not_write():
    api = MockAccount()
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("2", "down")
        await pilot.pause(0.1)
        assert app.bot_id == 103
        assert not app.query_one("#activate-bot", Button).disabled
        await pilot.click("#activate-bot")
        await pilot.pause(0.1)
        assert isinstance(app.screen, Confirm)
        assert api.calls == []
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert api.calls == []
        await pilot.click("#activate-bot")
        await pilot.pause(0.1)
        await pilot.click("#accept-confirm")
        await pilot.pause(0.3)
        assert api.calls == [("activate", 103)]
        assert next(b for b in app.submissions if b["id"] == 103)["status"] == "active"


async def test_challenge_defaults_to_practice_and_requires_confirmation():
    api = MockAccount()
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.2)
        app.prepare_challenge(2)
        await pilot.pause(0.1)
        app.query_one("#challenge-maps").select(1)
        await pilot.click("#review-challenge")
        await pilot.pause(0.1)
        assert isinstance(app.screen, Confirm)
        assert api.calls == []
        await pilot.click("#accept-confirm")
        await pilot.pause(0.3)
        assert api.calls == [("challenge", 2, False, [1])]


async def test_ranked_disables_map_selection_and_warns():
    api = MockAccount()
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.2)
        app.prepare_challenge(2)
        app.query_one("#challenge-mode", Select).value = "ranked"
        await pilot.pause(0.1)
        assert app.query_one("#challenge-maps").disabled
        await pilot.click("#review-challenge")
        await pilot.pause(0.1)
        assert "ranked" in app.screen.heading
        assert "rating" in app.screen.message
        await pilot.press("escape")
        assert api.calls == []


async def test_folder_upload_is_prepared_before_confirmation(tmp_path):
    (tmp_path / "bot.toml").write_text('[project]\nlanguage="python"\ninclude=["*.py"]\n')
    (tmp_path / "main.py").write_text("raise RuntimeError('must never execute')")
    api = MockAccount()
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.2)
        app.action_view("upload")
        await pilot.pause(0.1)
        app.query_one("#upload-path", Input).value = str(tmp_path)
        app.query_one("#upload-name", Input).value = "fixture-v5"
        await pilot.click("#review-upload")
        await pilot.pause(0.3)
        assert isinstance(app.screen, Confirm)
        assert api.calls == []
        await pilot.click("#accept-confirm")
        await pilot.pause(0.3)
        assert api.calls[0][:4] == ("upload", "fixture-v5", "", "python")
        assert api.calls[0][4].startswith(b"PK")


async def test_demo_write_is_blocked():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause(0.2)
        app.prepare_challenge(2)
        await pilot.click("#review-challenge")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, Confirm)
        assert not app.mutating


async def test_failed_refresh_keeps_existing_snapshot():
    api = MockAccount()
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.2)
        api.failed = True
        app.action_refresh()
        await pilot.pause(0.3)
        assert app.team["team"]["name"] == "Night shift"
        assert app.query_one("#overview-games", DataTable).row_count == 6
        assert "STALE" in str(app.query_one("#connection", Static).render())
