import asyncio
from importlib.resources import files

import httpx
import pytest
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Input,
    OptionList,
    Select,
    Static,
    TabbedContent,
    Tree,
)

from battlecode_cli.app import BattlecodeApp
from battlecode_cli.demo import DemoAPI
from battlecode_cli.dialogs import Confirm
from battlecode_cli.guide import NOTES
from battlecode_cli.map_editor import (
    EditorBoard,
    MapEditor,
    new_map,
    normalized_counts,
    preview_replay,
    validate_map,
)
from battlecode_cli.replay_viewer import ReplayViewer
from battlecode_cli.site import (
    DOC_SLUGS,
    MAX_HTML,
    NAVIGATION,
    PAGES,
    WebsiteClient,
    bundled_doc,
    directory_rows,
    named_record,
    parse_page,
    public_matches,
    safe_url,
)

HTML = """<html><nav>Excluded navigation</nav><script>notExecuted()</script><main>
<h1>Global battles</h1><p>Public results</p><table>
<tr><th>Time</th><th>Type</th><th>Team</th><th>Score</th><th>Team</th><th></th></tr>
<tr><td>Today</td><td>Ranked · Autoscrim</td><td>[red]Team A</td><td>3 – 2</td><td>Team B</td><td><a href='/battles/123'>Watch</a></td></tr>
</table><a href='/docs/map-files'>Map format</a><script>secretScript()</script>
<pre>MAP 8 8\nTILE_COUNT 0</pre></main></html>"""


def test_full_reference_navigation_has_arena_immediately_after_submissions():
    assert [label for _, label in PAGES] == [
        "Overview",
        "Updates",
        "Leaderboard",
        "Ratings",
        "Battles",
        "Games",
        "Tournaments",
        "Submissions",
        "Arena",
        "Documentation",
        "Visualiser",
        "Map editor",
        "Team",
        "Your battles",
        "Your games",
        "Find a team",
        "Account / API keys",
    ]
    build = next(items for group, items in NAVIGATION if group == "Build")
    assert [label for _, label, _ in build][:2] == ["Submissions", "Arena"]
    assert len(DOC_SLUGS) == 23
    assert set(NOTES) == DOC_SLUGS
    for slug in DOC_SLUGS:
        document = bundled_doc(slug)
        assert document.text.startswith("# ")
        assert "/docs/" + slug in document.text
        assert "not cached yet" not in document.text


def test_public_parser_ignores_scripts_navigation_and_keys_and_keeps_structured_rows():
    key = "bc_fixture_private_secret_0123456789"
    page = parse_page(HTML.replace("Public results", key))
    assert "Excluded navigation" not in page.text
    assert "notExecuted" not in page.text and "secretScript" not in page.text
    assert key not in page.text
    assert "```\nMAP 8 8\nTILE_COUNT 0" in page.text
    assert public_matches(page, "battles")[0]["id"] == 123
    assert public_matches(page, "games") == []
    assert "[↗](https://game.battlecode.au/docs/map-files)" in page.text
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("https://evil.example/") is None
    assert safe_url("/teams/1") == "https://game.battlecode.au/teams/1"
    assert safe_url("/team?key=" + key) is None
    with pytest.raises(ValueError, match="2 MB"):
        parse_page("x" * (MAX_HTML + 1))
    with pytest.raises(ValueError, match="readable"):
        parse_page("<script>nothingToDisplay()</script>")
    games = parse_page(
        "<main><h1>Games</h1><table><tr><td><time datetime='2026-10-02T12:00:00Z'></time></td><td>Ranked</td><td>Alpha</td><td>W – L</td><td>Beta</td><td>Portals</td><td><a href='/battles/456'>Watch</a></td></tr></table></main>"
    )
    assert public_matches(games, "games")[0]["id"] == 456
    assert games.rows[0].cells[0] != ""
    assert named_record(None) == "n/a"
    assert (
        directory_rows({"teams": [{"team": {"id": 1, "name": "Team"}, "rank": 5}]})[0]["rank"] == 5
    )


async def test_public_client_is_credential_free_cached_bounded_and_does_not_follow_redirects():
    calls = []

    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://evil.example/"})
        return httpx.Response(200, text=HTML)

    client = WebsiteClient(httpx.MockTransport(handler))
    try:
        await client.page("/battles")
        await client.page("/battles")
        assert len(calls) == 1
        await client.page("/battles", force=True)
        assert len(calls) == 2
        with pytest.raises(ValueError, match="HTTP 302"):
            await client.page("/redirect")
        assert len(calls) == 3
        with pytest.raises(ValueError):
            await client.page("//evil.example/")
    finally:
        await client.close()


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_all_native_website_pages_navigate_and_local_tools_work_offline(size):
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.2)
        for ident, _ in PAGES:
            app.action_view(ident)
            await pilot.pause(0.06)
            assert app.query_one("#pages", ContentSwitcher).current == (
                "games" if ident == "my-games" else ident
            )
            assert app.site_page == ident
        assert app.query_one("#teams-table", DataTable).row_count == 9
        assert app.query_one("#tournaments-table", DataTable).row_count == 1
        app.action_view("games")
        await pilot.pause(0.1)
        assert app.query_one("#game-tabs", TabbedContent).active == "game-global"
        app.query_one("#public-games-mode", Select).value = "unranked"
        await pilot.pause(0.1)
        assert app.query_one("#public-games-table", DataTable).row_count == 2
        app.action_view("ratings")
        await pilot.pause(0.1)
        assert app.query_one("#ratings-table", DataTable).content_region.height >= 3
        assert (
            app.query_one("#ratings-refresh", Button).region.bottom
            <= app.query_one("#pages").region.bottom
        )
        app.action_view("map-editor")
        await pilot.pause(0.1)
        assert app.query_one("#editor-status", Static).region.height >= 1
        assert app.query_one("#editor-viewport").content_region.height >= 5
        assert (
            app.query_one("#editor-save", Button).region.bottom
            <= app.query_one("#pages").region.bottom
        )
        app.action_view("documentation")
        app.query_one("#docs-search", Input).value = "portal"
        await pilot.pause(0.1)
        assert app.query_one("#docs-topics", OptionList).option_count > 1
        app.show_doc("map-files")
        assert app.current_doc == "map-files"
        await pilot.press("ctrl+b")
        await pilot.pause(0.1)
        assert app.query_one("#sidebar").region.width == 3
        await pilot.click("#sidebar-collapse")
        await pilot.pause(0.1)
        assert not app.has_class("sidebar-collapsed")
        await pilot.press("ctrl+t")
        assert app.theme == "battlecode-site-light"
        app.action_view("visualiser")
        await pilot.pause(0.1)
        app.query_one("#visualiser-sample", Button).scroll_visible(animate=False)
        await pilot.pause(0.1)
        await pilot.click("#visualiser-sample")
        await pilot.pause(0.1)
        assert isinstance(app.screen, ReplayViewer)


async def test_profile_members_bracket_and_own_battle_links_are_functional():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.15)
        app.action_view("team")
        await pilot.pause(0.1)
        assert app.query_one("#profile-members", DataTable).row_count == 2
        assert "harper" in str(app.query_one("#profile-members", DataTable).get_row_at(0)[0])
        app.action_view("my-battles")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.battle_id == 720
        assert app.query_one("#game-parts", DataTable).row_count == 5
        app.action_view("tournaments")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.15)
        tree = app.query_one("#tournament-bracket", Tree)
        assert "Synthetic example cup" in str(tree.root.label)
        assert len(tree.root.children) == 3
        app.open_public_team(1)
        await pilot.pause(0.15)
        assert "Northstar" in str(app.query_one("#profile-name", Static).render())
        assert not app.query_one("#profile-challenge", Button).disabled


def test_editor_accepts_all_unchanged_official_maps_and_retains_end_marker():
    for resource in files("battlecode_cli").joinpath("assets/maps").iterdir():
        if resource.name.endswith(".map"):
            text = resource.read_text()
            validate_map(text)
            normalized = normalized_counts(text)
            validate_map(normalized)
            if "END" in text.splitlines():
                assert normalized.splitlines()[-1] == "END"


def test_editor_validates_counts_portal_pairs_starting_bodies_and_source_bounds():
    text = new_map(8, 8)
    assert validate_map(text).width == 8
    assert preview_replay(text).frames[0].stats[0].queen == 2
    changed = normalized_counts(text + "TILE 3 3 8 20\nEDGE 1 2 4\nEDGE 2 2 4\n")
    assert "TILE_COUNT 1" in changed
    assert "EDGE_COUNT 2" in changed
    assert validate_map(changed)
    assert (3, 3) in preview_replay(changed).map.fountains
    with pytest.raises(ValueError, match="Portal"):
        validate_map(normalized_counts(text + "EDGE 1 2 4\n"))
    with pytest.raises(ValueError, match="orientation"):
        validate_map(normalized_counts(text + "EDGE 1 2 4\nEDGE 9 2 4\n"))
    with pytest.raises(ValueError, match="at least two"):
        validate_map(text.replace("DRAGON 0 2 1 1 0 1", "DRAGON 0 1 1 1"))
    with pytest.raises(ValueError, match="Unknown"):
        validate_map(text + "INVALID 2\n")
    with pytest.raises(ValueError, match="1 MB"):
        validate_map(text + "#" + "a" * 1024 * 1024)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_map(normalized_counts(text + "TILE 3 3 8 20\nTILE 3 3 8 20\n"))


@pytest.mark.parametrize("size", [(80, 24), (130, 40)])
async def test_editor_keyboard_brush_undo_and_confirmed_local_save(size, tmp_path):
    app = BattlecodeApp(demo=True)
    destination = tmp_path / "created.map"
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.15)
        app.action_view("map-editor")
        await pilot.pause(0.1)
        editor = app.query_one(MapEditor)
        app.query_one("#editor-tool", Select).value = "spawn"
        board = app.query_one(EditorBoard)
        board.focus()
        await pilot.press("right", "down", "enter")
        await pilot.pause(0.1)
        assert "TILE 1 1 8 20" in editor.text
        editor.pressed(Button.Pressed(app.query_one("#editor-undo", Button)))
        assert "TILE 1 1 8 20" not in editor.text
        editor.pressed(Button.Pressed(app.query_one("#editor-redo", Button)))
        assert "TILE 1 1 8 20" in editor.text
        app.query_one("#editor-path", Input).value = str(destination)
        app.query_one("#editor-save", Button).scroll_visible(animate=False)
        await pilot.pause(0.1)
        await pilot.click("#editor-save")
        await pilot.pause(0.1)
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")
        assert not destination.exists()
        await pilot.click("#editor-save")
        await pilot.pause(0.1)
        await pilot.click("#accept-confirm")
        await pilot.pause(0.15)
        assert destination.is_file()
        assert validate_map(destination.read_text()).width == 16


async def test_site_account_fields_clear_and_pending_profile_cannot_cross_account_switch():
    waiting = asyncio.Event()
    started = asyncio.Event()

    class FixtureAPI(DemoAPI):
        async def get(self, path):
            if path == "/teams/1":
                started.set()
                await waiting.wait()
            return await super().get(path)

    app = BattlecodeApp(api=FixtureAPI())
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.15)
        app.action_view("teams")
        await pilot.pause(0.1)
        app.action_view("tournaments")
        await pilot.pause(0.1)
        app.open_public_team(1)
        await started.wait()
        await app.replace_api(DemoAPI())
        waiting.set()
        await pilot.pause(0.1)
        assert app.public_profile is None and app.public_profile_id is None
        assert app.site_cache == {}
        assert app.query_one("#profile-members", DataTable).row_count == 0
        assert app.query_one("#tournaments-table", DataTable).row_count == 0
        assert "Northstar" not in str(app.query_one("#profile-name", Static).render())


async def test_editor_add_to_arena_needs_confirmation_and_never_starts_execution():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.1)
        app.action_view("map-editor")
        await pilot.pause(0.1)
        button = app.query_one("#editor-arena", Button)
        button.focus()
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not any("custom" in item["origins"] for item in app.arena_maps.entries())
        button.focus()
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.click("#accept-confirm")
        await pilot.pause(0.15)
        assert app.query_one("#pages", ContentSwitcher).current == "arena"
        assert app.query_one("#arena-map-source", Select).value == "custom"
        assert sum("custom" in item["origins"] for item in app.arena_maps.entries()) == 1
        assert app.arena_runner is None and app.arena_store.entries() == []


async def test_editor_rejects_changed_destination_between_review_and_save(tmp_path):
    destination = tmp_path / "changed.map"
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.1)
        app.action_view("map-editor")
        await pilot.pause(0.1)
        app.query_one("#editor-path", Input).value = str(destination)
        app.query_one("#editor-save", Button).focus()
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, Confirm)
        destination.write_text("External file created during review")
        await pilot.click("#accept-confirm")
        await pilot.pause(0.1)
        assert destination.read_text() == "External file created during review"
        assert "Destination changed" in str(app.query_one("#editor-status", Static).render())


async def test_simulations_appear_in_your_games_and_saved_labels_are_used(tmp_path):
    app = BattlecodeApp(demo=True)
    path = tmp_path / "simulation.replay"
    path.write_bytes(files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes())
    entry, replay = app.library.import_file(
        path, mode="Simulation", bot_names=("Alpha version", "Beta version")
    )
    job = {
        "id": "a" * 32,
        "seed": 0,
        "created_at": "2026-10-01T00:00:00Z",
        "status": "complete",
        "total": 1,
        "bots": [{"label": "Alpha version"}, {"label": "Beta version"}],
        "games": [
            {
                "index": 0,
                "a": 0,
                "b": 1,
                "map": "Sample",
                "summary": replay.summary(),
                "error": None,
                "library_id": entry["id"],
            }
        ],
        "maps": [],
    }
    app.arena_store.save(job)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(0.2)
        app.action_view("my-games")
        app.query_one("#games-filter", Select).value = "simulation"
        await pilot.pause(0.2)
        table = app.query_one("#games-table", DataTable)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[2]) == "Simulation"
        table.focus()
        await pilot.press("enter")
        await pilot.pause(0.15)
        assert isinstance(app.screen, ReplayViewer)
        assert app.screen.replay.bots == ("Alpha version", "Beta version")
