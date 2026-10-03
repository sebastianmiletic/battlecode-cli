import pytest
from rich.text import Text
from textual.widgets import Button, ContentSwitcher, DataTable, OptionList, Static

from battlecode_cli.app import BattlecodeApp
from battlecode_cli.site import PAGES
from battlecode_cli.typography import PageTitle, SectionHeading


@pytest.mark.parametrize("size", [(80, 24), (130, 40), (150, 60)])
async def test_type_scale_section_spacing_and_navigation_remain_accessible(size):
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        title = app.query_one("#site-title", PageTitle)
        assert title.label == "Night shift"
        assert len(title.render().plain.splitlines()) == (1 if size[1] < 32 else 3)
        summary = app.query_one("#overview-summary-heading", SectionHeading)
        assert summary.render().plain == "TEAM SUMMARY"
        quota = app.query_one("#sidebar-quota", Static)
        assert quota.content_size.width >= len(quota.render().plain)
        if size[0] >= 130:
            table = app.query_one("#overview-games", DataTable)
            assert not table.show_horizontal_scrollbar
        nav = app.query_one("#nav", OptionList)
        for group in ("compete", "build", "manage"):
            option = nav.get_option("group-" + group)
            assert option.disabled and option.prompt.plain.startswith("\n")
        assert nav.get_option("bots").prompt.plain.endswith("\n") == (size[1] >= 50)
        assert app.nav_indices["arena"] == app.nav_indices["bots"] + 1
        assert app.screen.can_view_entire(app.query_one("#projects-link", Button))
        for page, _ in PAGES:
            app.action_view(page)
            await pilot.pause()
            assert app.query_one("#pages", ContentSwitcher).current == (
                "games" if page == "my-games" else page
            )
            assert title.label
            if page == "map-editor" and size[1] >= 40:
                assert app.query_one("#editor-viewport").content_size.height >= 6
        app.action_toggle_sidebar()
        await pilot.pause()
        assert app.screen.can_view_entire(app.query_one("#sidebar-collapse", Button))
        assert await pilot.click("#sidebar-collapse")
        await pilot.pause()
        assert not app.has_class("sidebar-collapsed")


async def test_long_and_non_latin_titles_keep_their_actual_text_and_light_theme():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        title = app.query_one("#site-title", PageTitle)
        for label in (
            "A team name that is much too long for cell lettering",
            "海龍チーム",
            "[red] literal",
        ):
            title.update(Text(label))
            await pilot.pause()
            assert title.render().plain == label
            assert title.tooltip == label
        app.action_toggle_site_theme()
        await pilot.pause()
        assert app.theme == "battlecode-site-light"
        assert title.render().plain == "[red] literal"
