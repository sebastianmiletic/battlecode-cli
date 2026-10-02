"""Generate synthetic screenshots without reading keys or the user's replay library."""

import asyncio
import tempfile
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from battlecode_cli.app import BattlecodeApp
from battlecode_cli.config import AccountStore
from battlecode_cli.replay_viewer import ReplayViewer
from battlecode_cli.replays import ReplayLibrary, decode_replay


async def main():
    destination = Path(__file__).resolve().parents[1] / "docs"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = BattlecodeApp(demo=True, library=ReplayLibrary(root / "replays"))
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause(0.3)
            app.status("DEMO · synthetic data · read-only · ? help")
            await pilot.pause(0.1)
            app.save_screenshot("dashboard.svg", path=str(destination))
            replay = decode_replay(
                files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
            )
            app.push_screen(ReplayViewer(replay, "Synthetic sample"))
            await pilot.pause(0.3)
            app.save_screenshot("replay.svg", path=str(destination))
        with patch("battlecode_cli.app.importable_credential", return_value=None):
            setup = BattlecodeApp(
                accounts=AccountStore(root / "accounts"), library=ReplayLibrary(root / "replays")
            )
            async with setup.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.3)
                setup.save_screenshot("setup.svg", path=str(destination))


if __name__ == "__main__":
    asyncio.run(main())
