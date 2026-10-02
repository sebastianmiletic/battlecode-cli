"""Generate synthetic screenshots without reading keys or user data libraries."""

import asyncio
import tempfile
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from textual.widgets import TabbedContent

from battlecode_cli.app import BattlecodeApp
from battlecode_cli.arena import ArenaStore, MapLibrary
from battlecode_cli.config import AccountStore
from battlecode_cli.replay_viewer import ReplayViewer
from battlecode_cli.replays import ReplayLibrary, decode_replay


def fixture_app(root: Path, *, demo: bool = True) -> BattlecodeApp:
    app = BattlecodeApp(
        demo=demo, accounts=AccountStore(root / "accounts"), library=ReplayLibrary(root / "replays")
    )
    app.arena_maps = MapLibrary(root / "maps")
    app.arena_store = ArenaStore(root / "runs")
    return app


async def main():
    destination = Path(__file__).resolve().parents[1] / "docs"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = fixture_app(root)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause(0.3)
            app.status("DEMO · synthetic data · ? help")
            await pilot.pause(0.1)
            app.save_screenshot("dashboard.svg", path=str(destination))
            for page in ("bots", "games", "leaderboard"):
                app.action_view(page)
                await pilot.pause(0.2)
                app.save_screenshot(f"{page}.svg", path=str(destination))
            app.action_view("arena")
            replay = decode_replay(
                files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
            )
            job = {
                "id": "a" * 32,
                "created_at": "2026-10-02T00:00:00Z",
                "status": "complete",
                "total": 4,
                "seed": 42,
                "bots": [{"label": "1: version-A"}, {"label": "2: version-B"}],
                "maps": [{"id": "fixture", "name": "Sample"}],
                "games": [],
            }
            for index, winner in enumerate(("A", "A", "B", "A")):
                summary = dict(replay.summary(), winner=winner)
                job["games"].append(
                    {
                        "index": index,
                        "a": index % 2,
                        "b": 1 - index % 2,
                        "seed": 42 + index // 2,
                        "map": "Sample",
                        "map_id": "fixture",
                        "summary": summary,
                        "diagnostics": replay.diagnostics,
                        "error": None,
                    }
                )
            app.arena_store.save(job)
            app.arena_job = job
            app.render_arena_runs()
            app.render_arena_results()
            app.query_one("#arena-tabs", TabbedContent).active = "arena-results"
            await pilot.pause(0.2)
            app.save_screenshot("arena.svg", path=str(destination))
            app.push_screen(ReplayViewer(replay, "Synthetic sample"))
            await pilot.pause(0.3)
            app.save_screenshot("replay.svg", path=str(destination))
        compact = fixture_app(root)
        async with compact.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            compact.save_screenshot("compact.svg", path=str(destination))
        with patch("battlecode_cli.app.importable_credential", return_value=None):
            setup = fixture_app(root, demo=False)
            async with setup.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.3)
                setup.save_screenshot("setup.svg", path=str(destination))
    for path in destination.glob("*.svg"):
        content = path.read_text(encoding="utf-8")
        path.write_text(
            "\n".join(line.rstrip() for line in content.splitlines()) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
