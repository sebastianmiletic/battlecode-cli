"""Arena interactions, independent of account mutation controls."""

from __future__ import annotations

import asyncio
import copy
import csv
import io
import json
import shutil
import tempfile
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.message_pump import MessagePump
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Select,
    SelectionList,
    Static,
    TabbedContent,
)

from .arena import (
    ArenaError,
    ArenaPlan,
    ArenaRunner,
    ArenaStore,
    MapLibrary,
    aggregates,
    bot_version,
    opponent_versions,
    report,
    run_process,
    runner_path,
)
from .config import atomic_write, download_dir, redact
from .dialogs import Confirm, FilePicker, TextViewer
from .models import date_label, record_label, rows, winrate
from .replays import path_value


class ArenaActions(MessagePump):
    def init_arena(self) -> None:
        self.arena_maps = MapLibrary()
        self.arena_boot_error = ""
        try:
            self.arena_maps.ensure_official()
        except (ValueError, OSError) as error:
            self.arena_boot_error = redact(str(error))
        self.arena_store = ArenaStore()
        self.arena_runner = None
        self.arena_busy = False
        self.arena_job = None
        self.arena_running_id = None
        self.arena_game_index = None
        self.arena_rendered_id = None
        self.arena_map_entries = []

    def update_arena_runner(self) -> None:
        available = bool(runner_path())
        self.query_one("#arena-runner-state", Static).update(
            self.arena_boot_error
            or (
                "Official judge sandbox · local only"
                if available
                else "Install the official runner to simulate locally."
            )
        )
        self.query_one("#arena-install", Button).display = not available
        self.query_one("#arena-run", Button).disabled = self.arena_busy
        self.query_one("#arena-stop", Button).disabled = self.arena_runner is None
        self.query_one("#arena-stop-results", Button).disabled = self.arena_runner is None

    def render_arena_maps(self, *, select_all: bool = False) -> None:
        try:
            self.arena_map_entries = self.arena_maps.entries()
            mode = self.query_one("#arena-map-source", Select).value
            selection = self.query_one("#arena-local-maps", SelectionList)
            previous = set(selection.selected)
            selected = [
                entry
                for entry in self.arena_map_entries
                if mode == "all" or mode in entry.get("origins", [])
            ]
            selection.clear_options()
            selection.add_options(
                [
                    (
                        Text(f"{entry['name']} · {entry['width']}×{entry['height']}"),
                        entry["id"],
                        select_all or entry["id"] in previous,
                    )
                    for entry in selected
                ]
            )
        except (ValueError, OSError) as error:
            self.fail(error, "#arena-progress")

    @on(Select.Changed, "#arena-map-source")
    def arena_map_source_changed(self) -> None:
        self.render_arena_maps(select_all=True)

    @work(group="arena-maps", exclusive=True)
    async def import_arena_maps(self) -> None:
        value = self.query_one("#arena-map-path", Input).value
        if not value.strip():
            self.query_one("#arena-progress", Static).update("Choose a maps folder, ZIP or file.")
            return
        try:
            self.query_one("#arena-progress", Static).update("Checking and copying custom maps…")
            result = await asyncio.to_thread(self.arena_maps.import_path, value)
            self.query_one("#arena-map-source", Select).value = "custom"
            self.render_arena_maps(select_all=True)
            message = f"{result['added']} added · {result['duplicates']} duplicates · {len(result['rejected'])} rejected"
            self.query_one("#arena-progress", Static).update(message)
            if result["rejected"]:
                self.push_screen(TextViewer("Map import issues", "\n".join(result["rejected"])))
        except Exception as error:
            self.fail(error, "#arena-progress")

    @work(group="arena-maps", exclusive=True)
    async def get_official_maps(self) -> None:
        try:
            self.query_one("#arena-progress", Static).update("Loading official maps…")
            blobs = []
            if not self.api.demo and self.api.credential and self.account_is_current():
                api = self.api
                raw = await api.get("/maps")
                self.require_same_account(api)
                for entry in rows(raw, "maps"):
                    text = entry.get("text", entry.get("content", entry.get("map")))
                    if isinstance(text, str):
                        blobs.append((str(entry.get("name", "Map")), text.encode()))
            if blobs:
                result = await asyncio.to_thread(
                    self.arena_maps.import_blobs, blobs, origin="official"
                )
            else:
                command = runner_path()
                if not command:
                    raise ArenaError(
                        "Connect an API key or install the runner to get official maps."
                    )
                with tempfile.TemporaryDirectory(prefix="battlecode-maps-") as temporary:
                    path = Path(temporary)
                    code, output = await run_process(
                        [command, "maps", str(path)], cwd=path, timeout=60, cancel=asyncio.Event()
                    )
                    if code:
                        raise ArenaError(
                            output[-1000:] or "Could not load the runner's bundled maps."
                        )
                    result = await asyncio.to_thread(
                        self.arena_maps.import_path, str(path), origin="official"
                    )
            self.query_one("#arena-map-source", Select).value = "official"
            self.render_arena_maps(select_all=True)
            self.query_one("#arena-progress", Static).update(
                f"{result['added']} official maps added · {result['duplicates']} already stored"
            )
        except Exception as error:
            self.fail(error, "#arena-progress")

    @work(group="arena-run")
    async def review_arena(self) -> None:
        if self.arena_busy:
            return
        self.arena_busy = True
        self.update_arena_runner()
        try:
            if not runner_path():
                raise ArenaError("Install the official runner first.")
            values = [self.query_one(f"#arena-bot-{side}", Input).value for side in ("a", "b")]
            if not all(value.strip() for value in values):
                raise ArenaError("Choose both bot versions before starting.")
            opponents_path = self.query_one("#arena-opponents", Input).value
            selected = set(self.query_one("#arena-local-maps", SelectionList).selected)
            bots = await asyncio.to_thread(
                lambda: [
                    *(bot_version(value) for value in values),
                    *opponent_versions(opponents_path),
                ]
            )
            plan = ArenaPlan(
                tuple(bots),
                tuple(entry for entry in self.arena_map_entries if entry["id"] in selected),
                repeats=int(self.query_one("#arena-repeats", Input).value),
                swap=self.query_one("#arena-swap", Checkbox).value,
                seed=int(self.query_one("#arena-seed", Input).value),
                timeout=int(self.query_one("#arena-timeout", Input).value),
            )
            plan.validate()
            if not await self.push_screen_wait(
                Confirm(
                    "Run local simulation?",
                    f"{bots[0].label} vs {bots[1].label}\n{len(bots) - 2} shared opponents · {len(plan.maps)} maps · {plan.repeats} repeat(s)\n{plan.count:,} games · {'both seats' if plan.swap else 'one seat'} · base seed {plan.seed}\n\nBots are copied into a private workspace and run in the official judge sandbox. This can use substantial CPU and disk. No uploads, activations, challenges or ELO changes. Stop preserves completed results.",
                    "Run simulation",
                )
            ):
                return
            self.arena_runner = ArenaRunner(self.arena_store, self.arena_maps)
            self.arena_job = None
            self.arena_rendered_id = None
            self.arena_game_index = None
            self.update_arena_runner()
            self.query_one("#arena-tabs", TabbedContent).active = "arena-results"
            await self.arena_runner.run(plan, self.arena_progress)
            self.render_library()
        except Exception as error:
            self.fail(error, "#arena-progress")
        finally:
            self.arena_busy = False
            self.arena_runner = None
            self.arena_running_id = None
            if self.is_running:
                self.update_arena_runner()
                self.render_arena_runs()

    def arena_progress(self, job: dict) -> None:
        if not self.is_running:
            return
        self.arena_running_id = job["id"]
        self.arena_job = job
        self.query_one("#arena-progress", Static).update(
            f"{job['status'].upper()} · {len(job['games'])}/{job['total']} games\n{job['current']}"
        )
        self.render_arena_runs()
        self.render_arena_results()

    def render_arena_runs(self) -> None:
        try:
            jobs = self.arena_store.entries()
            self.fill(
                self.query_one("#arena-runs-table", DataTable),
                [
                    (
                        job["id"],
                        (
                            date_label(job["created_at"]),
                            "interrupted"
                            if job["status"] == "running" and job["id"] != self.arena_running_id
                            else job["status"],
                            f"{job['completed']}/{job['total']}",
                            job["bots"][0]["label"],
                            job["bots"][1]["label"],
                        ),
                    )
                    for job in jobs
                ],
            )
        except Exception as error:
            self.fail(error, "#arena-progress")

    @on(DataTable.RowSelected, "#arena-runs-table")
    def arena_run_selected(self, event: DataTable.RowSelected) -> None:
        if self.arena_busy:
            self.notify("Stop the current simulation before opening an older batch.")
            return
        self.load_arena_run(str(event.row_key.value))

    @work(group="arena-results", exclusive=True)
    async def load_arena_run(self, ident: str) -> None:
        try:
            self.arena_job = await asyncio.to_thread(self.arena_store.load, ident)
            self.arena_game_index = None
            self.render_arena_results()
        except Exception as error:
            self.fail(error, "#arena-summary")

    def render_arena_results(self) -> None:
        job = self.arena_job
        if not job:
            return
        stats = aggregates(job)
        errors = sum(bool(game.get("error")) for game in job["games"])
        self.query_one("#arena-summary", Static).update(
            f"SIMULATION · {job['status'].upper()} · {len(job['games'])}/{job['total']} games · {errors} errors\n"
            + "   |   ".join(
                f"{row['label']}: {record_label(row)} ({winrate(row)})" for row in stats[:2]
            )
        )
        table = self.query_one("#arena-results-table", DataTable)
        if self.arena_rendered_id != job["id"]:
            table.clear()
            self.arena_rendered_id = job["id"]
        for game in job["games"][table.row_count :]:
            summary = game.get("summary", {})
            metrics = game.get("diagnostics", {})
            winner = summary.get("winner")
            winner_label = (
                "ERROR"
                if game.get("error")
                else "DRAW"
                if winner is None
                else job["bots"][game["a"] if winner == "A" else game["b"]]["label"]
            )
            moves = "/".join(str(metrics.get(side, {}).get("moves", 0)) for side in ("A", "B"))
            deaths = "/".join(
                str(sum(metrics.get(side, {}).get("deaths", {}).values())) for side in ("A", "B")
            )
            table.add_row(
                *[
                    Text(redact(str(value)))
                    for value in (
                        game["index"] + 1,
                        game["map"],
                        job["bots"][game["a"]]["label"],
                        job["bots"][game["b"]]["label"],
                        winner_label,
                        summary.get("rounds", "n/a"),
                        moves,
                        deaths,
                    )
                ],
                key=str(game["index"]),
            )
        self.query_one("#arena-report", Button).disabled = False
        self.query_one("#arena-export", Button).disabled = False
        if table.row_count and self.arena_game_index is None:
            self.arena_game_index = 0
        self.update_arena_replay_button()

    def update_arena_replay_button(self) -> None:
        ready = bool(
            self.arena_job
            and self.arena_game_index is not None
            and self.arena_game_index < len(self.arena_job["games"])
            and self.arena_job["games"][self.arena_game_index].get("summary")
        )
        button = self.query_one_optional("#arena-replay", Button)
        if button:
            button.disabled = not ready

    @on(DataTable.RowHighlighted, "#arena-results-table")
    def arena_game_highlight(self, event: DataTable.RowHighlighted) -> None:
        self.arena_game_index = int(str(event.row_key.value))
        self.update_arena_replay_button()

    @on(DataTable.RowSelected, "#arena-results-table")
    def arena_game_selected(self, event: DataTable.RowSelected) -> None:
        self.arena_game_index = int(str(event.row_key.value))
        self.view_arena_replay()

    def view_arena_replay(self) -> None:
        if self.arena_job and self.arena_game_index is not None:
            path = (
                self.arena_store.path(self.arena_job["id"])
                / f"game-{self.arena_game_index:05}.replay"
            )
            if path.is_file():
                self.open_replay_file(str(path), import_file=True)
            else:
                self.notify("No replay was saved for this game.")

    @work(group="arena-export")
    async def export_arena(self, value: str) -> None:
        if not self.arena_job:
            return
        job = copy.deepcopy(self.arena_job)
        try:
            destination = path_value(value)
            if destination.exists() and not destination.is_dir():
                raise ArenaError("Choose an export folder, not a file.")
            name = f"arena-{job['id'][:8]}"
            paths = [destination / f"{name}.{extension}" for extension in ("json", "csv")]
            if not await self.push_screen_wait(
                Confirm(
                    "Export benchmark?",
                    "\n".join(str(path) for path in paths)
                    + "\n\nExisting files with these names will be replaced. No files are sent online.",
                    "Export",
                )
            ):
                return
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(
                [
                    "game",
                    "map",
                    "bot_a",
                    "bot_b",
                    "seed",
                    "winner",
                    "rounds",
                    "moves_a",
                    "moves_b",
                    "error",
                ]
            )

            def cell(value):
                value = redact(str(value))
                return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value

            for game in job["games"]:
                summary, metrics = game.get("summary", {}), game.get("diagnostics", {})
                writer.writerow(
                    [
                        cell(value)
                        for value in (
                            game["index"] + 1,
                            game["map"],
                            job["bots"][game["a"]]["label"],
                            job["bots"][game["b"]]["label"],
                            game["seed"],
                            "error" if game.get("error") else summary.get("winner") or "draw",
                            summary.get("rounds", ""),
                            metrics.get("A", {}).get("moves", 0),
                            metrics.get("B", {}).get("moves", 0),
                            game.get("error") or "",
                        )
                    ]
                )
            await asyncio.to_thread(atomic_write, paths[0], json.dumps(job, indent=2).encode())
            await asyncio.to_thread(atomic_write, paths[1], output.getvalue().encode())
            self.notify(str(destination), title="JSON and CSV exported")
        except Exception as error:
            self.fail(error)

    @work(group="arena-install")
    async def install_arena_runner(self) -> None:
        command = shutil.which("uv")
        if self.arena_busy:
            return
        if not command:
            self.fail(
                ArenaError(
                    "Install uv from https://docs.astral.sh/uv/getting-started/installation/, then uv tool install --python 3.13 unswbc"
                ),
                "#arena-progress",
            )
            return
        if not await self.push_screen_wait(
            Confirm(
                "Install official runner?",
                "Install unswbc with uv into your user account? It includes the engine, Python sandbox and C/C++ toolchain, about 200 MB installed. This downloads software, not your bots or API keys.",
                "Install runner",
            )
        ):
            return
        try:
            self.query_one("#arena-install", Button).disabled = True
            self.query_one("#arena-progress", Static).update("Installing the official runner…")
            code, output = await run_process(
                [command, "tool", "install", "--python", "3.13", "unswbc>=1.2.2,<2"],
                cwd=Path.home(),
                timeout=600,
                cancel=asyncio.Event(),
            )
            if code:
                raise ArenaError(output[-1600:] or "Runner installation failed.")
            self.update_arena_runner()
            self.query_one("#arena-progress", Static).update(
                "Runner installed. Get official maps, then choose two bots."
            )
        except Exception as error:
            self.fail(error, "#arena-progress")
        finally:
            self.query_one("#arena-install", Button).disabled = False

    @work(group="download", exclusive=True)
    async def bot_to_arena(self, side: str) -> None:
        if self.api.demo or self.bot_id is None or not self.account_is_current():
            return
        api, ident = self.api, self.bot_id
        try:
            path = await api.download(
                f"/submissions/{ident}/download",
                download_dir() / f"submission-{ident}.zip",
                max_bytes=4 * 1024 * 1024,
            )
            self.require_same_account(api)
            self.query_one(f"#arena-bot-{side}", Input).value = str(path)
            self.action_view("arena")
            self.query_one("#arena-tabs", TabbedContent).active = "arena-local"
            self.status(f"Submission #{ident} selected as Arena bot {side.upper()}.")
        except Exception as error:
            self.fail(error)

    @on(Button.Pressed)
    def arena_button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id
        if ident in ("arena-use-a", "arena-use-b"):
            self.bot_to_arena(ident[-1])
        elif ident in (
            "arena-browse-a",
            "arena-browse-b",
            "arena-browse-opponents",
            "arena-browse-maps",
        ):
            field = {
                "arena-browse-a": "arena-bot-a",
                "arena-browse-b": "arena-bot-b",
                "arena-browse-opponents": "arena-opponents",
                "arena-browse-maps": "arena-map-path",
            }[ident]

            def picked(path):
                if path:
                    self.query_one(f"#{field}", Input).value = path

            self.push_screen(
                FilePicker(
                    self.query_one(f"#{field}", Input).value,
                    kind="maps" if ident.endswith("maps") else "bot",
                ),
                picked,
            )
        elif ident == "arena-import-maps":
            self.import_arena_maps()
        elif ident == "arena-official-maps":
            self.get_official_maps()
        elif ident in ("arena-select-all", "arena-clear-maps"):
            selection = self.query_one("#arena-local-maps", SelectionList)
            selection.select_all() if ident == "arena-select-all" else selection.deselect_all()
        elif ident == "arena-run":
            self.review_arena()
        elif ident in ("arena-stop", "arena-stop-results") and self.arena_runner:
            self.arena_runner.cancel.set()
            self.query_one("#arena-progress", Static).update(
                "Stopping. Completed results are kept."
            )
        elif ident == "arena-install":
            self.install_arena_runner()
        elif ident == "arena-report" and self.arena_job:
            self.push_screen(TextViewer("Benchmark report", report(self.arena_job)))
        elif ident == "arena-replay":
            self.view_arena_replay()
        elif ident == "arena-export" and self.arena_job:
            self.push_screen(
                FilePicker(str(Path.home() / "Downloads"), kind="directory"),
                lambda path: self.export_arena(path) if path else None,
            )
