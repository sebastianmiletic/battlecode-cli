"""Generate a README screenshot from synthetic data; never uses account credentials."""

import asyncio
from pathlib import Path

from battlecode_cli.app import BattlecodeApp


async def main():
    app = BattlecodeApp(demo=True)
    async with app.run_test(size=(130, 38)) as pilot:
        await pilot.pause(0.3)
        app.status("DEMO · synthetic data · read-only · auto-refresh 45s · ? help")
        await pilot.pause(0.1)
        path = Path(__file__).resolve().parents[1] / "docs"
        path.mkdir(exist_ok=True)
        app.save_screenshot("dashboard.svg", path=str(path))


if __name__ == "__main__":
    asyncio.run(main())
