import asyncio
import time
import uuid
from importlib.resources import files

import pytest
from textual.widgets import Button, ContentSwitcher, DataTable, Digits, Input, Select, TabbedContent

from battlecode_cli.app import PAGES, BattlecodeApp
from battlecode_cli.arena import ArenaRunner
from battlecode_cli.charts import HistoryChart
from battlecode_cli.demo import DemoAPI
from battlecode_cli.dialogs import Confirm, TextViewer
from battlecode_cli.history import HistoryStore, histories, series
from battlecode_cli.models import leaderboard_rows, members_label, winrate
from battlecode_cli.replay_viewer import ReplayViewer
from battlecode_cli.replays import decode_replay


async def wait_for_screen(app, pilot, screen_type):
    # Preparing snapshots can outlast a short UI pause on Windows.
    async with asyncio.timeout(10):
        while not isinstance(app.screen, screen_type) or not app.screen.is_mounted:
            await pilot.pause(0.05)
        await pilot.pause()


def test_history_uses_server_elo_and_marks_reconstructed_rank_as_inferred():
    teams = [
        {
            "id": 1,
            "ranked": True,
            "history": [
                {"date": "2026-10-01T00:00:00Z", "elo": 1500},
                {"date": "2026-10-02T00:00:00Z", "elo": 1700},
            ],
        },
        {
            "id": 2,
            "ranked": True,
            "history": [
                {"date": "2026-10-01T00:00:00Z", "elo": 1600},
                {"date": "2026-10-02T00:00:00Z", "elo": 1650},
            ],
        },
        {"id": 3, "ranked": False, "history": [{"date": "2026-10-01T00:00:00Z", "elo": 3000}]},
    ]
    elo, ranks, inferred = histories({"id": 1}, teams)
    assert [value for _, value in elo] == [1500, 1700]
    assert [value for _, value in ranks] == [2, 1]
    assert inferred
    teams[0]["history"][0]["rank"] = 7
    teams[0]["history"][1]["rank"] = 4
    assert [value for _, value in histories({"id": 1}, teams)[1]] == [7, 4]
    assert not histories({"id": 1}, teams)[2]
    assert histories({}, []) == ([], [], False)
    assert (
        series([{"date": "bad", "elo": 4}, {"date": "2026-10-01", "elo": float("nan")}], "elo")
        == []
    )


def test_observed_history_is_persistent_bounded_and_isolated_by_team(tmp_path):
    store = HistoryStore(tmp_path)
    assert store.observe({"id": 1, "elo": 1500}, 10, date="2026-10-01T00:00:00Z")[0]["rank"] == 10
    store.observe({"id": 1, "elo": 1501}, 9, date="2026-10-01T01:00:00Z")
    assert len(HistoryStore(tmp_path).observe({"id": 1, "elo": 1502}, 8)) == 3
    assert len(store.observe({"id": 2, "elo": 2000}, 1)) == 1
    assert store.observe({"id": "../../escape", "elo": 9}, 2) == []


def test_win_rates_require_a_real_denominator_and_leaderboard_adapts_nested_teams():
    assert winrate({"wins": 100}) == "n/a"
    assert winrate({"wins": 0, "draws": 0, "losses": 0}) == "n/a"
    assert winrate({"record": {"wins": 6, "draws": 2, "losses": 2}}) == "60.0%"
    entries = leaderboard_rows(
        {
            "teams": [
                {"team": {"id": 2, "name": "Beta"}, "rank": 2, "elo": 1500},
                {"id": 1, "rank": 1, "elo": 1700},
                {"name": "bad"},
            ]
        }
    )
    assert [entry["id"] for entry in entries] == [1, 2]
    assert members_label({"members": [{"username": "alice"}, "bob"]}) == "alice, bob"


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_website_pages_large_metrics_charts_merged_tabs_and_projects_link(size, monkeypatch):
    opened = []
    monkeypatch.setattr("battlecode_cli.app.webbrowser.open", opened.append)
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.2)
        pages = [page for page, _ in PAGES]
        assert len(pages) == 17
        assert pages[pages.index("bots") + 1] == "arena"
        assert "documentation" in pages and "map-editor" in pages
        assert app.query_one("#elo-value", Digits).value == "1834"
        assert app.query_one("#rank-value", Digits).value == "17"
        for ident in ("elo-history", "rank-history"):
            chart = app.query_one(f"#{ident}", HistoryChart)
            assert len(chart.points) == 30
            chart.scroll_visible(animate=False)
            await pilot.pause(0.1)
            assert chart.region.bottom <= app.query_one("#pages").region.bottom
        assert "CONTROL ROOM" not in app.export_screenshot()
        assert await pilot.click("#projects-link")
        assert opened == ["https://github.com/sebastianmiletic/battlecode-cli"]
        await pilot.press("2")
        assert app.query_one("#pages", ContentSwitcher).current == "bots"
        await pilot.click(app.query_one("#bot-tabs", TabbedContent).get_tab("bot-upload"))
        await pilot.pause(0.1)
        assert app.query_one("#upload-path", Input).visible
        app.action_view("my-games")
        await pilot.pause(0.1)
        app.query_one("#games-filter", Select).value = "ranked"
        await pilot.pause(0.1)
        assert app.query_one("#games-table", DataTable).row_count == 4
        app.query_one("#games-filter", Select).value = "unranked"
        await pilot.pause(0.1)
        assert app.query_one("#games-table", DataTable).row_count == 2
        assert str(app.query_one("#games-table", DataTable).get_row_at(0)[2]) == "Unranked"
        await pilot.click(app.query_one("#game-tabs", TabbedContent).get_tab("game-local"))
        await pilot.pause(0.1)
        assert app.query_one("#replay-path", Input).visible
        await pilot.press("4")
        await pilot.pause(0.1)
        button = app.query_one("#arena-run", Button)
        assert button.region.bottom <= app.query_one("#pages").region.bottom
        assert await pilot.click(button)
        await pilot.pause(0.1)
        assert not isinstance(app.screen, Confirm)


async def test_live_leaderboard_all_teams_member_search_and_explicit_refresh():
    class LiveFixture(DemoAPI):
        def __init__(self):
            self.calls = []

        async def get(self, path):
            self.calls.append(path)
            if path == "/leaderboard":
                return {
                    "teams": [
                        {
                            "id": index + 1,
                            "rank": index + 1,
                            "name": f"Team {index}",
                            "elo": 2400 - index,
                            "wins": 100,
                            "members": [{"username": f"user{index}"}],
                        }
                        for index in range(300)
                    ]
                }
            return await super().get(path)

    api = LiveFixture()
    app = BattlecodeApp(api=api)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.2)
        app.action_view("leaderboard")
        await pilot.pause(0.1)
        table = app.query_one("#ladder-table", DataTable)
        assert table.row_count == 300
        assert str(table.get_row_at(0)[3]) == "n/a"  # wins alone cannot give win rate
        app.query_one("#ladder-search", Input).value = "user299"
        await pilot.pause(0.1)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[1]) == "Team 299"
        count = api.calls.count("/leaderboard")
        await pilot.click("#leaderboard-refresh")
        await pilot.pause(0.2)
        assert api.calls.count("/leaderboard") == count + 1


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_arena_review_cancel_results_and_report_without_server_mutations(
    size, tmp_path, monkeypatch
):
    import battlecode_cli.arena_ui as arena_ui

    monkeypatch.setattr(arena_ui, "runner_path", lambda: "fixture-runner")
    prepare = arena_ui.bot_version

    def slow_prepare(*args, **kwargs):
        time.sleep(0.35)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(arena_ui, "bot_version", slow_prepare)
    executed = []

    class FixtureRunner(ArenaRunner):
        async def run(self, plan, progress):
            executed.append(plan)
            raw = files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
            replay = decode_replay(raw)
            ident = uuid.uuid4().hex
            root = self.store.path(ident)
            root.mkdir(parents=True)
            (root / "game-00000.replay").write_bytes(raw)
            job = {
                "id": ident,
                "created_at": "2026-10-02T00:00:00Z",
                "status": "complete",
                "current": "",
                "total": plan.count,
                "seed": plan.seed,
                "bots": [{"label": "Alpha"}, {"label": "Beta"}],
                "maps": list(plan.maps),
                "games": [
                    {
                        "index": 0,
                        "a": 0,
                        "b": 1,
                        "seed": 0,
                        "map": "Fixture",
                        "map_id": plan.maps[0]["id"],
                        "summary": replay.summary(),
                        "diagnostics": replay.diagnostics,
                        "error": None,
                    }
                ],
            }
            self.store.save(job)
            progress(job)
            await asyncio.sleep(0)
            return job

    monkeypatch.setattr(arena_ui, "ArenaRunner", FixtureRunner)
    for name in ("alpha", "beta"):
        source = tmp_path / name
        source.mkdir()
        (source / "bot.toml").write_text('[project]\nlanguage="python"\ninclude=["*.py"]\n')
        (source / "main.py").write_text("raise RuntimeError('not executed')")
    app = BattlecodeApp(demo=True)
    app.arena_maps.import_blobs(
        [("fixture.map", b"MAP 6 4\nDRAGON 0 2 1 1 0 1\nDRAGON 1 2 4 2 5 2\n")], origin="official"
    )
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.2)
        app.action_view("arena")
        app.render_arena_maps(select_all=True)
        app.query_one("#arena-bot-a", Input).value = str(tmp_path / "alpha")
        app.query_one("#arena-bot-b", Input).value = str(tmp_path / "beta")
        await pilot.pause(0.1)
        assert await pilot.click("#arena-run")
        await wait_for_screen(app, pilot, Confirm)
        assert executed == []
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert executed == []
        assert await pilot.click("#arena-run")
        await wait_for_screen(app, pilot, Confirm)
        await pilot.click("#accept-confirm")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(executed) == 1
        assert app.query_one("#arena-tabs", TabbedContent).active == "arena-results"
        assert app.query_one("#arena-results-table", DataTable).row_count == 1
        assert not app.query_one("#arena-replay", Button).disabled
        await pilot.click("#arena-report")
        await wait_for_screen(app, pilot, TextViewer)
        assert "PER-MAP RECORDS" in app.screen.text
        await pilot.press("escape")
        await pilot.click("#arena-replay")
        await wait_for_screen(app, pilot, ReplayViewer)
        await pilot.press("escape")
        assert len(app.library.entries()) == 1


async def test_deferred_navigation_focus_does_not_reopen_hidden_arena_form():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        app.action_view("arena")
        tabs = app.query_one("#arena-tabs", TabbedContent)
        tabs.active = "arena-results"
        await pilot.pause(0.2)
        assert tabs.active == "arena-results"
        assert tabs.get_pane("arena-results").display
        app.action_view("overview")
        await pilot.pause(0.1)
        app.action_view("arena")
        await pilot.pause(0.2)
        assert tabs.active == "arena-results"
        assert app.focused is app.query_one("#arena-results-table", DataTable)


async def test_exports_confirm_and_escape_formula_text(tmp_path):
    import csv
    import json

    app = BattlecodeApp(demo=True)
    app.arena_job = {
        "id": "b" * 32,
        "status": "complete",
        "seed": 0,
        "bots": [{"label": "+alpha"}, {"label": "beta"}],
        "games": [
            {"index": 0, "map": "=danger", "a": 0, "b": 1, "seed": 0, "error": "fixture failure"}
        ],
    }
    destination = tmp_path / "export"
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.2)
        app.export_arena(str(destination))
        await wait_for_screen(app, pilot, Confirm)
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert not destination.exists()
        app.export_arena(str(destination))
        await wait_for_screen(app, pilot, Confirm)
        await pilot.click("#accept-confirm")
        await app.workers.wait_for_complete()
        structured = json.loads((destination / "arena-bbbbbbbb.json").read_text())
        assert len(structured["games"]) == 1
        with (destination / "arena-bbbbbbbb.csv").open(newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[1][1] == "'=danger"
        assert rows[1][2] == "'+alpha"
        assert rows[1][5] == "error"
