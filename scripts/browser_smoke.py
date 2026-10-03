"""Synthetic, isolated browser verification. Optional official-sandbox templates smoke.

uv run --with playwright python -m playwright install chromium
uv run --with playwright python scripts/browser_smoke.py
Pass --sandbox-templates PATH only to test two synthetic C++ starter bots in unswbc.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import zipfile
from contextlib import ExitStack
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from playwright.async_api import async_playwright

from battlecode_cli import arena, config, history, replays, web
from battlecode_cli.arena import ArenaRunner
from battlecode_cli.web import Dashboard, LocalServer


async def verify(root: Path, output: Path, templates: Path | None):
    errors = []
    with ExitStack() as stack:
        for module in (config, arena, history, replays):
            stack.enter_context(patch.object(module, "data_dir", lambda: root / "data"))
        stack.enter_context(patch.object(config, "config_dir", lambda: root / "config"))
        if templates is None:
            fixture = root / "sample.json"
            fixture.write_bytes(
                files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
            )
            fake = root / "runner.py"
            fake.write_text(
                "import pathlib, sys\n"
                + f"pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(pathlib.Path({str(fixture)!r}).read_bytes())\n"
            )
            stack.enter_context(patch.object(web, "runner_path", lambda: "fixture-runner"))
            stack.enter_context(
                patch.object(
                    web,
                    "ArenaRunner",
                    lambda store, maps, **kwargs: ArenaRunner(
                        store, maps, [sys.executable, str(fake)], **kwargs
                    ),
                )
            )
        for name in ("Synthetic Alpha", "Synthetic Beta"):
            project = root / name
            project.mkdir()
            language = "cpp" if templates else "python"
            (project / "bot.toml").write_text(
                f'[project]\nlanguage="{language}"\ninclude=["*.py","*.cpp","*.hpp"]\n'
            )
            if templates:
                for filename in ("main.cpp", "helper.hpp"):
                    (project / filename).write_bytes((templates / "cpp" / filename).read_bytes())
            else:
                (project / "main.py").write_text(
                    "raise RuntimeError('source must never execute on host')\n"
                )
        server = LocalServer(Dashboard(demo=True))
        server.start()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                context = await browser.new_context(
                    viewport={"width": 1512, "height": 982}, device_scale_factor=1
                )
                page = await context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "console",
                    lambda message: (
                        errors.append(message.text) if message.type == "error" else None
                    ),
                )
                await page.goto(server.url)
                await page.wait_for_function(
                    "() => document.querySelector('#metric-elo').textContent === '1834'"
                )
                await page.evaluate("document.fonts.ready")
                assert await page.locator(".brand img").evaluate(
                    "image => image.naturalWidth === 292 && image.naturalHeight === 88"
                )
                assert await page.locator("#navigation a").count() == 6
                await page.screenshot(path=str(output / "web-dashboard.png"), full_page=True)
                await page.locator('nav a[data-page="bots"]').click()
                assert await page.locator("#bot-rows tr").count() == 4
                await page.get_by_role("tab", name="Upload", exact=True).click()
                await page.locator("#upload-name").fill("preserved draft")
                await page.locator('nav a[data-page="leaderboard"]').click()
                await page.locator("#leaderboard-search").fill("player8")
                assert await page.locator("#leaderboard-rows tr").count() == 1
                await page.locator('nav a[data-page="arena"]').click()
                assert await page.locator("#map-list input:checked").count() == 15
                await page.locator("#arena-a-path").fill(str(root / "Synthetic Alpha"))
                await page.locator("#arena-b-path").fill(str(root / "Synthetic Beta"))
                await page.locator("#maps-clear").click()
                digest = hashlib.sha256(
                    files("battlecode_cli").joinpath("assets/maps/default_small.map").read_bytes()
                ).hexdigest()[:24]
                await page.locator(f'#map-list input[value="{digest}"]').check()
                await page.locator("#arena-repeats").fill("1" if templates else "2")
                # Screenshots show synthetic labels, never the machine's temporary directory.
                await page.locator("#arena-a-path").fill("~/Bots/Synthetic Alpha")
                await page.locator("#arena-b-path").fill("~/Bots/Synthetic Beta")
                await page.screenshot(path=str(output / "web-arena.png"), full_page=True)
                await page.locator("#arena-a-path").fill(str(root / "Synthetic Alpha"))
                await page.locator("#arena-b-path").fill(str(root / "Synthetic Beta"))
                await page.locator("#run-simulation").click()
                await page.locator("#confirmation").wait_for(state="visible")
                assert await page.locator("#confirmation button:focus").text_content() == "Cancel"
                await (
                    page.locator("#confirmation")
                    .get_by_role("button", name="Cancel", exact=True)
                    .click()
                )
                assert server.backend.run_task is None
                await page.locator("#run-simulation").click()
                await page.locator("#confirm-accept").click()
                await page.wait_for_function(
                    "() => document.querySelector('#run-heading').textContent.includes('Simulation · complete')",
                    timeout=300000 if templates else 20000,
                )
                expected = 2 if templates else 4
                assert len(server.backend.current["games"]) == expected
                assert all(not game.get("error") for game in server.backend.current["games"]), (
                    server.backend.current["games"]
                )
                assert server.backend.library.entries()[0]["mode"] == "Simulation"
                await page.locator("#run-games button[data-replay]").first.click()
                await page.locator("#player").wait_for(state="visible")
                await page.locator("#replay-last").click()
                await page.wait_for_function(
                    "() => document.querySelector('#replay-timeline').value === document.querySelector('#replay-timeline').max"
                )
                assert "Round" in await page.locator("#replay-position").text_content()
                await page.locator("#replay-first").click()
                await page.locator("#replay-next").click()
                await page.wait_for_function(
                    "() => document.querySelector('#replay-timeline').value === '1'"
                )
                await page.locator("#replay-timeline").fill("2")
                await page.wait_for_function(
                    "() => document.querySelector('#replay-position').textContent.startsWith('Frame 2/')"
                )
                await page.locator("#toast").wait_for(state="hidden")
                assert (
                    "Synthetic" in await page.locator("#player-bot-a").text_content()
                    if templates
                    else True
                )
                await page.screenshot(path=str(output / "web-replay.png"))
                await page.locator("#player-close").click()
                await page.locator("#export-csv").click()
                async with page.expect_download() as download:
                    await page.locator("#confirm-accept").click()
                exported = await download.value
                await exported.save_as(output / "synthetic-results.csv")
                assert "Simulation" in (output / "synthetic-results.csv").read_text()
                await page.locator('nav a[data-page="games"]').click()
                await page.locator("#games-filter").select_option("Simulation")
                await page.wait_for_function(
                    f"() => document.querySelectorAll('#game-rows button[data-replay]').length === {expected}"
                )
                assert "Unranked" not in await page.locator("#game-rows").text_content()
                await page.get_by_role("tab", name="Saved replays").click()
                assert (
                    await page.locator("#replay-rows").get_by_text("Simulation", exact=True).count()
                    >= 1
                )
                await page.locator("#sample-replay").click()
                await page.locator("#replay-play").click()
                await page.wait_for_function(
                    "() => Number(document.querySelector('#replay-timeline').value) > 0"
                )
                await page.keyboard.press("Escape")
                await page.locator('nav a[data-page="bots"]').click()
                assert await page.locator("#upload-name").input_value() == "preserved draft"
                await page.locator('nav a[data-page="arena"]').click()
                await page.get_by_role("tab", name="Simulation", exact=True).click()
                custom = root / "custom maps.zip"
                with zipfile.ZipFile(custom, "w") as archive:
                    for index in range(3):
                        archive.writestr(
                            f"map-{index}.map",
                            f"MAP 6 4\nMAP_NAME Custom {index}\nDRAGON 0 2 1 1 0 1\nDRAGON 1 2 4 2 5 2\n",
                        )
                    archive.writestr("bad.map", "invalid map")
                await page.get_by_text("Import custom maps", exact=True).click()
                await page.locator('button[data-file="maps"]').click()
                await page.locator("#file-picker").set_input_files(custom)
                await page.wait_for_function(
                    "() => document.querySelector('#map-import-path').value.length > 0"
                )
                await page.locator("#import-maps").click()
                await page.locator("#confirmation").wait_for(state="visible")
                assert "3 valid maps" in await page.locator("#confirm-text").text_content()
                await page.locator("#confirm-accept").click()
                await page.wait_for_function(
                    "() => document.querySelectorAll('#map-list input:checked').length === 3"
                )
                await page.locator('nav a[data-page="overview"]').click()
                await page.locator("#toast").wait_for(state="hidden")
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(path=str(output / "web-mobile.png"), full_page=True)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    "Page overflows the mobile viewport"
                )
                await page.locator('nav a[data-page="arena"]').click()
                await page.locator("#arena-a-path").fill("~/Bots/Synthetic Alpha")
                await page.locator("#arena-b-path").fill("~/Bots/Synthetic Beta")
                await page.locator("#map-import-path").fill("~/Maps/custom maps.zip")
                await page.screenshot(path=str(output / "web-arena-mobile.png"), full_page=True)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await context.close()
                await browser.close()
        finally:
            server.close()
    assert not errors, errors
    print(
        json.dumps(
            {
                "browser_checks": "passed",
                "official_sandbox": bool(templates),
                "games": expected,
                "screenshots": str(output),
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs"))
    parser.add_argument("--sandbox-templates", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="battlecode-browser-check-") as temporary:
        asyncio.run(verify(Path(temporary), args.output, args.sandbox_templates))


if __name__ == "__main__":
    main()
