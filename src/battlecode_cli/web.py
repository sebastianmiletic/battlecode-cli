"""Loopback-only browser dashboard. Credentials and simulation execution stay in Python."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .api import APIError, BattlecodeAPI
from .arena import (
    MAX_IMPORT_BYTES,
    ArenaError,
    ArenaPlan,
    ArenaRunner,
    ArenaStore,
    BotVersion,
    MapLibrary,
    aggregates,
    bot_version,
    opponent_versions,
    report,
    run_process,
    runner_path,
)
from .bots import MAX_ZIP_BYTES as MAX_ZIP
from .bots import prepare_bot
from .config import KEY_PATTERN, AccountStore, Credential, atomic_write, redact
from .demo import DemoAPI
from .history import HistoryStore, histories, series
from .models import active_bot, battle_row, leaderboard_rows, rows, team_data, winrate
from .replays import MAX_FILE, ReplayLibrary, clean_label, load_replay, parse_map

ASSETS = files("battlecode_cli").joinpath("assets/web")


def identifier(value) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,9}", str(value)):
        raise ValueError("Choose a valid server ID.")
    return int(value)


def geometry(board) -> dict:
    return {
        "name": board.name,
        "width": board.width,
        "height": board.height,
        "horizontal": [[x, y, edge] for (x, y), edge in board.horizontal.items()],
        "vertical": [[x, y, edge] for (x, y), edge in board.vertical.items()],
        "fountains": sorted(board.fountains),
        "queens": board.queens,
        "dragons": [asdict(dragon) for dragon in board.initial],
    }


@dataclass
class Approval:
    expires: float
    api: object
    active_id: str | None
    operation: Callable[[], Awaitable[dict]]


class Dashboard:
    def __init__(self, *, demo: bool = False, api=None, refresh: int = 45):
        self.accounts = AccountStore()
        self.api = (
            api
            if api is not None
            else DemoAPI()
            if demo
            else BattlecodeAPI(self.accounts.active_credential())
        )
        self.demo = self.api.demo
        self.custom_api = api is not None
        self.refresh_seconds = max(15, refresh)
        self.maps = MapLibrary()
        self.map_error = ""
        try:
            self.maps.ensure_official()
        except (ValueError, OSError) as error:
            self.map_error = clean_label(error, 500)
        self.runs = ArenaStore()
        self.library = ReplayLibrary()
        self.history = HistoryStore()
        self.team, self.submissions, self.battles, self.ladder, self.online_maps = (
            {},
            [],
            [],
            [],
            [],
        )
        self.error = ""
        self.last_attempt = 0.0
        self.updated = None
        self.auth_failed = False
        self.cooldown = 0.0
        self.refresh_lock = asyncio.Lock()
        self.auth_lock = asyncio.Lock()
        self.approvals: dict[str, Approval] = {}
        self.temporary = tempfile.TemporaryDirectory(prefix="battlecode-browser-")
        self.staging = Path(self.temporary.name)
        if self.staging.exists():
            self.staging.chmod(0o700)
        self.runner = None
        self.run_task = None
        self.current = None
        self.install_task = None
        self.install_cancel = asyncio.Event()
        self.install_message = ""
        self.replay_cache = {}
        self.closed = False

    def clear_account_data(self) -> None:
        self.team, self.submissions, self.battles, self.ladder, self.online_maps = (
            {},
            [],
            [],
            [],
            [],
        )
        self.approvals.clear()
        self.last_attempt, self.updated, self.auth_failed, self.cooldown = 0, None, False, 0
        self.error = ""

    async def replace_api(self, credential) -> None:
        old = self.api
        self.api = BattlecodeAPI(credential)
        self.clear_account_data()
        await old.close()

    async def guard_account(self, expected=None) -> None:
        if not self.demo and not self.custom_api and self.api.credential:
            if self.accounts.active_id != self.api.credential.profile_id:
                await self.replace_api(None)
                self.error = (
                    "Account changed outside this dashboard. Reconnect a saved key in API keys."
                )
                raise APIError(self.error, 409)
        if expected is not None and expected is not self.api:
            raise APIError("Account changed. Review this action again; nothing was sent.", 409)

    async def refresh_data(self, *, force: bool = False) -> None:
        async with self.refresh_lock:
            await self.guard_account()
            if not self.demo and not self.api.credential:
                return
            if time.monotonic() < self.cooldown:
                return
            if not force and (
                self.auth_failed or time.monotonic() - self.last_attempt < self.refresh_seconds
            ):
                return
            api = self.api
            self.last_attempt = time.monotonic()
            paths = ["/team", "/submissions", "/battles?limit=50", "/leaderboard", "/maps"]
            values = await asyncio.gather(
                *(api.get(path) for path in paths), return_exceptions=True
            )
            await self.guard_account(api)
            errors = []
            for path, value in zip(paths, values, strict=True):
                if isinstance(value, Exception):
                    errors.append(clean_label(value, 500))
                    if isinstance(value, APIError):
                        if value.status == 401:
                            self.auth_failed = True
                        if value.status == 429:
                            self.cooldown = time.monotonic() + value.retry_after
                    continue
                if path == "/team":
                    self.team = value if isinstance(value, dict) else {}
                elif path == "/submissions":
                    self.submissions = sorted(
                        rows(value, "submissions"), key=lambda row: row.get("id", 0), reverse=True
                    )
                elif path.startswith("/battles"):
                    self.battles = rows(value, "battles", "matches")
                elif path == "/leaderboard":
                    self.ladder = leaderboard_rows(value)
                else:
                    self.online_maps = rows(value, "maps")
                    blobs = []
                    for entry in self.online_maps:
                        text = entry.get("text", entry.get("content", entry.get("map")))
                        if isinstance(text, str):
                            blobs.append((str(entry.get("name", "Map")), text.encode()))
                    if blobs:
                        try:
                            await asyncio.to_thread(
                                self.maps.import_blobs, blobs, origin="official"
                            )
                        except (ValueError, OSError) as error:
                            self.map_error = clean_label(error, 500)
            self.error = errors[0] if errors else ""
            if not errors:
                self.auth_failed = False
                self.updated = time.time()

    async def state(self) -> dict:
        try:
            await self.refresh_data()
        except APIError as error:
            self.error = str(error)
        team = team_data(self.team)
        rank = self.team.get("rank", team.get("rank"))
        elo, ranks, inferred = histories(team, self.ladder)
        origin = rank_origin = "Server history"
        if not self.demo and team:
            observed = await asyncio.to_thread(self.history.observe, team, rank)
            if not elo:
                elo = series(observed, "elo")
                origin = "Locally observed history"
            if not ranks:
                ranks = series(observed, "rank")
                rank_origin = "Locally observed history"
                inferred = False
        profiles = [] if self.demo else self.accounts.accounts()
        active = None if self.demo else self.accounts.active_id
        local_errors = []
        local = {}
        for key, store in (("maps", self.maps), ("runs", self.runs), ("replays", self.library)):
            try:
                local[key] = await asyncio.to_thread(store.entries)
            except (ValueError, OSError) as error:
                local[key] = []
                local_errors.append(clean_label(error, 500))
        return {
            "demo": self.demo,
            "connected": self.demo or self.api.credential is not None,
            "error": clean_label(self.error or self.map_error or "\n".join(local_errors), 500),
            "updated": self.updated,
            "refresh": self.refresh_seconds,
            "team": team,
            "rank": rank,
            "active_bot": active_bot(self.submissions),
            "history": {
                "elo": elo,
                "rank": ranks,
                "inferred": inferred,
                "origin": origin,
                "rank_origin": rank_origin,
            },
            "submissions": self.submissions,
            "battles": [battle_row(battle, team.get("id")) for battle in self.battles],
            "leaderboard": [{**row, "winrate": winrate(row)} for row in self.ladder],
            "online_maps": self.online_maps,
            **local,
            "running": self.run_view(self.current) if self.current else None,
            "arena_busy": bool(self.run_task and not self.run_task.done()),
            "runner": bool(runner_path()),
            "installing": bool(self.install_task and not self.install_task.done()),
            "install_message": self.install_message,
            "accounts": [
                {
                    "id": profile.id,
                    "label": profile.label,
                    "team": profile.team_name,
                    "storage": profile.storage,
                    "active": profile.id == active,
                    "connected": bool(
                        self.api.credential and self.api.credential.profile_id == profile.id
                    ),
                }
                for profile in profiles
            ],
        }

    def run_view(self, job: dict) -> dict:
        games = []
        for game in job["games"]:
            summary = game.get("summary") or {}
            games.append(
                {
                    "index": game["index"],
                    "mode": "Simulation",
                    "map": game["map"],
                    "a": job["bots"][game["a"]]["label"],
                    "b": job["bots"][game["b"]]["label"],
                    "winner": summary.get("winner"),
                    "rounds": summary.get("rounds"),
                    "error": game.get("error"),
                    "seconds": game.get("seconds"),
                    "seed": str(game["seed"]),
                    "replay": f"run:{job['id']}:{game['index']}"
                    if summary and not game.get("error")
                    else None,
                    "library_error": game.get("library_error"),
                }
            )
        return {
            "id": job["id"],
            "mode": "Simulation",
            "status": job["status"],
            "total": job["total"],
            "current": job.get("current", ""),
            "created_at": job["created_at"],
            "games": games,
            "aggregates": aggregates(job),
            "error": job.get("error"),
        }

    async def get_run(self, ident: str) -> dict:
        self.runs.path(ident)
        if self.current and self.current["id"] == ident:
            return self.current
        return await asyncio.to_thread(self.runs.load, ident)

    def replay_path(self, ident: str) -> Path:
        if ident == "sample":
            return Path(str(files("battlecode_cli").joinpath("assets/sample.replay.json")))
        if ident.startswith("library:"):
            return self.library.path(ident.removeprefix("library:"))
        if match := re.fullmatch(r"run:([a-f0-9]{32}):([0-9]{1,5})", ident):
            return self.runs.path(match[1]) / f"game-{int(match[2]):05}.replay"
        raise ValueError("Choose a saved replay.")

    async def replay(self, ident: str, offset: int = 0, limit: int = 40) -> dict:
        path = self.replay_path(ident)
        if not 0 <= offset <= 2000 or not 1 <= limit <= 80:
            raise ValueError("Invalid replay frame range.")
        stamp = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        replay = self.replay_cache.get(stamp)
        if replay is None:
            replay = await asyncio.to_thread(load_replay, path)
            if len(self.replay_cache) >= 2:
                self.replay_cache.clear()
            self.replay_cache[stamp] = replay
        summary = replay.summary()
        if match := re.fullmatch(r"run:([a-f0-9]{32}):([0-9]{1,5})", ident):
            job = await self.get_run(match[1])
            game = next((game for game in job["games"] if game["index"] == int(match[2])), None)
            if game:
                summary["bots"] = {
                    "A": job["bots"][game["a"]]["label"],
                    "B": job["bots"][game["b"]]["label"],
                }
        elif ident.startswith("library:"):
            entry = next(
                (entry for entry in self.library.entries() if ident == f"library:{entry['id']}"),
                None,
            )
            if entry:
                summary["bots"] = {"A": entry["bot_a"], "B": entry["bot_b"]}
        return {
            "id": ident,
            "summary": summary,
            "map": geometry(replay.map),
            "diagnostics": replay.diagnostics,
            "offset": offset,
            "frames": [
                {
                    "round": frame.round,
                    "dragons": [asdict(dragon) for dragon in frame.dragons.values()],
                    "pearls": sorted(frame.pearls),
                    "stats": [asdict(stats) for stats in frame.stats],
                    "events": frame.events,
                }
                for frame in replay.frames[offset : offset + limit]
            ],
        }

    async def stage_file(self, kind: str, name: str, raw: bytes) -> dict:
        maximum = {"bot": MAX_ZIP, "maps": MAX_IMPORT_BYTES, "replay": MAX_FILE}.get(kind)
        if maximum is None or not raw or len(raw) > maximum:
            raise ValueError("File is empty or exceeds this import's size limit.")
        name = clean_label(Path(name.replace("\\", "/")).name, 120)
        suffix = Path(name).suffix.lower()
        allowed = {
            "bot": {".zip"},
            "maps": {".zip", ".map", ".txt"},
            "replay": {".replay", ".gz", ".json"},
        }[kind]
        if suffix not in allowed:
            raise ValueError("Choose a supported file type, or enter a local folder path.")
        # Temporary files are private and disappear when this dashboard closes.
        existing = list(self.staging.glob("upload-*"))
        size = sum(
            path.stat().st_size
            for folder in existing
            for path in folder.iterdir()
            if path.is_file()
        )
        if len(existing) >= 32 or size + len(raw) > 256 * 1024 * 1024:
            raise ValueError(
                "Temporary import space is full. Restart the browser dashboard to clear it."
            )
        path = self.staging / f"upload-{secrets.token_hex(8)}" / name
        await asyncio.to_thread(atomic_write, path, raw)
        return {"path": str(path), "name": name}

    async def choose_bot(self, value) -> BotVersion:
        if isinstance(value, dict) and "submission" in value:
            api = self.api
            await self.guard_account(api)
            ident = identifier(value["submission"])
            own = next((bot for bot in self.submissions if bot.get("id") == ident), None)
            if not own or self.demo:
                raise ValueError(
                    "Only your own uploaded source can be downloaded. Choose a local source in demo mode."
                )
            path = self.staging / f"own-{ident}-{secrets.token_hex(4)}.zip"
            await api.download(f"/submissions/{ident}/download", path, max_bytes=MAX_ZIP)
            await self.guard_account(api)
            prepared = await asyncio.to_thread(prepare_bot, str(path))
            return BotVersion(clean_label(own.get("name", f"Version {ident}")), prepared)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "Choose both bots: one of your uploaded versions, a ZIP, or a local project folder."
            )
        return await asyncio.to_thread(bot_version, value)

    async def review(self, body: dict) -> dict:
        await self.guard_account()
        kind = body.get("kind")
        api = self.api
        title, text, label = "", "", "Confirm"
        operation = None
        if kind == "simulation":
            if self.run_task and not self.run_task.done():
                raise ArenaError("A simulation is already running. Stop it or wait for completion.")
            if not runner_path():
                raise ArenaError("Install the official runner before starting a simulation.")
            bots = [await self.choose_bot(body.get(key)) for key in ("a", "b")]
            bots.extend(await asyncio.to_thread(opponent_versions, str(body.get("opponents", ""))))
            chosen = body.get("maps")
            if not isinstance(chosen, list) or not all(isinstance(item, str) for item in chosen):
                raise ValueError("Select at least one map.")
            entries = {entry["id"]: entry for entry in self.maps.entries()}
            if any(ident not in entries for ident in chosen) or len(chosen) != len(set(chosen)):
                raise ValueError("Map selection changed. Select maps and review again.")
            plan = ArenaPlan(
                tuple(bots),
                tuple(entries[ident] for ident in chosen),
                repeats=int(body.get("repeats", 1)),
                swap=body.get("swap", True) is True,
                seed=int(body.get("seed", 0)),
                timeout=int(body.get("timeout", 600)),
            )
            plan.validate()
            title, label = "Run simulation?", "Run simulation"
            text = f"{bots[0].label} vs {bots[1].label}\n{len(bots) - 2} shared opponents · {len(plan.maps)} maps · {plan.repeats} repeats per map\n{plan.count:,} games · {'both seats' if plan.swap else 'one seat'} · base seed {plan.seed}\n\nOfficial unswbc judge sandbox. Source and maps are snapshotted; every game replay is saved automatically. This uses local CPU and disk. No uploads, online challenges or ELO changes. Stop preserves finished games."

            async def simulate():
                if self.run_task and not self.run_task.done():
                    raise ArenaError("A simulation is already running.")
                self.current = None
                self.runner = ArenaRunner(self.runs, self.maps, library=self.library)
                self.error = ""

                async def execute():
                    try:
                        await self.runner.run(plan, lambda job: setattr(self, "current", job))
                    except (ValueError, OSError) as error:
                        self.error = clean_label(error, 500)
                    finally:
                        self.runner = None

                self.run_task = asyncio.create_task(execute())
                return {"started": True}

            operation = simulate
        elif kind == "maps":
            scratch = MapLibrary(self.staging / f"maps-{secrets.token_hex(6)}")
            result = await asyncio.to_thread(scratch.import_path, str(body.get("path", "")))
            blobs = [
                (entry["name"], scratch.path(entry["id"]).read_bytes())
                for entry in scratch.entries()
            ]
            shutil.rmtree(scratch.root)
            title, label = "Import custom maps?", "Import maps"
            text = (
                f"{len(blobs)} valid maps · {len(result['rejected'])} rejected\n\nLocal only. Nothing is uploaded.\n"
                + "\n".join(result["rejected"][:12])
            )

            async def import_maps():
                copied = await asyncio.to_thread(self.maps.import_blobs, blobs)
                return {**copied, "rejected": result["rejected"]}

            operation = import_maps
        elif kind == "install":
            if self.install_task and not self.install_task.done():
                raise ValueError("Runner installation is already in progress.")
            command = shutil.which("uv")
            if not command:
                candidate = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
                command = str(candidate) if candidate.is_file() else None
            if not command:
                raise ValueError(
                    "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
                )
            title, label = "Install official runner?", "Install runner"
            text = "Install unswbc 1.2.2 or newer in your user-level uv tools. Downloads approximately 200 MB, including the official engine and WASM toolchain. No administrator access, bot execution or Battlecode key is required."

            async def install():
                if self.install_task and not self.install_task.done():
                    raise ValueError("Installation is already running.")
                self.install_message = "Installing official runner…"
                self.install_cancel.clear()

                async def execute():
                    try:
                        code, output = await run_process(
                            [command, "tool", "install", "--python", "3.13", "unswbc>=1.2.2,<2"],
                            cwd=self.staging,
                            timeout=600,
                            cancel=self.install_cancel,
                        )
                        self.install_message = (
                            "Official runner installed."
                            if not code and runner_path()
                            else clean_label(output[-1000:] or "Installation failed.", 1000)
                        )
                    except (ValueError, OSError) as error:
                        self.install_message = clean_label(error, 1000)

                self.install_task = asyncio.create_task(execute())
                return {"started": True}

            operation = install
        elif kind in ("activate", "challenge", "upload"):
            if self.demo or not self.api.credential:
                raise ValueError("Connect an API key for online actions. Demo data cannot be sent.")
            if kind == "activate":
                ident = identifier(body.get("id"))
                own = next((row for row in self.submissions if row.get("id") == ident), None)
                if not own:
                    raise ValueError("Select one of your uploaded versions.")
                title, label = "Activate this bot?", "Activate bot"
                text = f"{clean_label(own.get('name', ident))}\n\nThis replaces your team's active server bot for future matches, including ranked matches."

                async def activate():
                    await api.activate(ident)
                    return {"sent": True}

                operation = activate
            elif kind == "challenge":
                ident = identifier(body.get("team"))
                ranked = body.get("ranked") is True
                selected = body.get("maps", [])
                if not isinstance(selected, list):
                    raise ValueError("Invalid online map selection.")
                map_ids = [identifier(value) for value in selected] if not ranked else []
                allowed = {entry.get("id") for entry in self.online_maps}
                if any(ident not in allowed for ident in map_ids):
                    raise ValueError("Choose an official online map.")
                title, label = (
                    ("Play ranked?", "Play ranked")
                    if ranked
                    else ("Send unranked challenge?", "Send challenge")
                )
                text = f"Opponent team {ident}\n\n" + (
                    "Five games on server-selected maps. This can change your ELO. Uses your active server bot, not a local source."
                    if ranked
                    else "No rating change. Uses your active server bot, not a local source. Server rate limits apply."
                )

                async def challenge():
                    await api.challenge(ident, ranked, map_ids)
                    return {"sent": True}

                operation = challenge
            else:
                prepared = await asyncio.to_thread(prepare_bot, str(body.get("path", "")))
                name = clean_label(body.get("name", ""), 120)
                description = str(body.get("description", ""))[:2000]
                if not name:
                    raise ValueError("Enter a version name.")
                title, label = "Upload this version?", "Upload bot"
                text = f"{name} · {prepared.language} · {len(prepared.blob):,} bytes\n\nSource is sent to Battlecode. A successful build may become active. No automatic retry."

                async def upload():
                    await api.upload(name, description, prepared.language, prepared.blob)
                    return {"sent": True}

                operation = upload
        elif kind in ("delete-account", "use-account", "disconnect"):
            if self.demo:
                raise ValueError("Account changes are disabled in demo mode.")
            profile = (
                self.accounts.resolve(str(body.get("id", ""))) if kind != "disconnect" else None
            )
            if kind == "delete-account":
                title, label = "Delete this saved key?", "Delete local key"
                text = f"{profile.label}\n\nDeletes this local credential only. No other saved key is connected. Revoke it on the Battlecode team page to disable it elsewhere."

                async def delete_account():
                    deleting_active = self.accounts.active_id == profile.id
                    self.accounts.delete(profile.id)
                    if deleting_active:
                        await self.replace_api(None)
                    return {"deleted": True}

                operation = delete_account
            elif kind == "use-account":
                title, label = "Connect this account?", "Connect account"
                text = f"{profile.label} · {profile.team_name}\n\nVerify the key, then replace the active connection. Old account data and pending approvals are cleared."

                async def use_account():
                    credential = self.accounts.credential(profile.id)
                    candidate = BattlecodeAPI(credential)
                    try:
                        await candidate.get("/me")
                        await self.guard_account(api)
                        self.accounts.activate(profile.id)
                    finally:
                        await candidate.close()
                    await self.replace_api(credential)
                    return {"connected": True}

                operation = use_account
            else:
                title, label = "Disconnect account?", "Disconnect"
                text = "Saved keys remain. No other saved or environment key connects automatically. Local simulations and replays remain available."

                async def disconnect():
                    self.accounts.disconnect()
                    await self.replace_api(None)
                    return {"disconnected": True}

                operation = disconnect
        elif kind == "remove-replay":
            ident = str(body.get("id", ""))
            self.library.path(ident)
            title, label = "Remove saved replay?", "Remove copy"
            text = "Removes the library copy only. Original imports and automatically saved Arena run files are preserved."

            async def remove_replay():
                await asyncio.to_thread(self.library.remove, ident)
                self.replay_cache.clear()
                return {"removed": True}

            operation = remove_replay
        elif kind == "export":
            job = await self.get_run(str(body.get("id", "")))
            # Freeze the result set shown at review, not a later in-progress result.
            frozen = json.loads(json.dumps(job))
            fmt = body.get("format", "json")
            if fmt not in ("json", "csv"):
                raise ValueError("Choose JSON or CSV.")
            title, label = "Export simulation results?", "Download results"
            text = f"{len(frozen['games'])} saved games as {fmt.upper()}. Includes records and diagnostics, not source archives or credentials. The browser will ask where to save the file."

            async def export():
                if fmt == "json":
                    content = json.dumps(frozen, indent=2)
                else:
                    output = io.StringIO(newline="")
                    writer = csv.writer(output)
                    writer.writerow(
                        [
                            "game",
                            "mode",
                            "map",
                            "bot_a",
                            "bot_b",
                            "winner",
                            "rounds",
                            "seed",
                            "error",
                        ]
                    )
                    for game in frozen["games"]:
                        summary = game.get("summary") or {}
                        cells = [
                            game["index"] + 1,
                            "Simulation",
                            game["map"],
                            frozen["bots"][game["a"]]["label"],
                            frozen["bots"][game["b"]]["label"],
                            "error" if game.get("error") else summary.get("winner") or "draw",
                            summary.get("rounds", ""),
                            str(game["seed"]),
                            game.get("error") or "",
                        ]
                        writer.writerow(
                            [
                                "'" + cell
                                if isinstance(cell, str)
                                and cell.lstrip().startswith(("=", "+", "-", "@"))
                                else cell
                                for cell in cells
                            ]
                        )
                    content = output.getvalue()
                return {"filename": f"simulation-{frozen['id']}.{fmt}", "content": content}

            operation = export
        else:
            raise ValueError("Unknown review action.")
        await self.guard_account(api)
        active = None if self.demo else self.accounts.active_id
        token = secrets.token_urlsafe(32)
        self.approvals.clear()
        self.approvals[token] = Approval(time.monotonic() + 180, api, active, operation)
        return {"approval": token, "title": title, "text": text, "label": label}

    async def approve(self, token: str) -> dict:
        approval = self.approvals.pop(token, None)
        if approval is None or approval.expires < time.monotonic():
            raise ValueError("Review expired or was already used. Review the action again.")
        await self.guard_account(approval.api)
        if not self.demo and self.accounts.active_id != approval.active_id:
            raise APIError("Account changed. Review the action again; nothing was sent.", 409)
        result = await approval.operation()
        if result.get("sent") or result.get("connected"):
            self.last_attempt = 0
        return result

    async def connect(self, body: dict) -> dict:
        if self.demo:
            raise ValueError("Account changes are disabled in demo mode.")
        token = str(body.get("key", "")).strip()
        if not KEY_PATTERN.fullmatch(token):
            raise ValueError("Use a Battlecode API key starting with bc_.")
        async with self.auth_lock:
            original = self.api
            candidate = BattlecodeAPI(Credential(token, "browser key entry"))
            try:
                who = await candidate.get("/me")
                await self.guard_account(original)
                activate = body.get("activate", True) is True
                profile = self.accounts.add(
                    token, str(body.get("label", "")), who, activate=activate
                )
                if activate:
                    await self.replace_api(self.accounts.credential(profile.id))
                return {"saved": True, "connected": activate, "label": profile.label}
            finally:
                await candidate.close()

    async def dispatch(self, method: str, target: str, body=None) -> dict:
        if self.closed:
            raise ValueError("Dashboard is shutting down.")
        path = urlsplit(target).path
        query = parse_qs(urlsplit(target).query)

        def first(key, default=""):
            return query.get(key, [default])[0]

        if method == "GET":
            if path == "/api/state":
                return await self.state()
            if path == "/api/run":
                job = await self.get_run(first("id"))
                return {**self.run_view(job), "report": report(job)}
            if path == "/api/simulations":
                offset, limit = int(first("offset", "0")), int(first("limit", "100"))
                if not 0 <= offset <= 1_000_000 or not 1 <= limit <= 100:
                    raise ValueError("Invalid simulation page.")
                entries = await asyncio.to_thread(self.runs.entries)
                total = sum(int(entry.get("completed", 0)) for entry in entries)
                selected, skipped = [], 0
                for entry in entries:
                    count = int(entry.get("completed", 0))
                    if skipped + count <= offset:
                        skipped += count
                        continue
                    job = await self.get_run(entry["id"])
                    games = list(reversed(self.run_view(job)["games"]))
                    start = max(0, offset - skipped)
                    selected.extend(
                        {**game, "at": job["created_at"], "run_id": job["id"]}
                        for game in games[start : start + limit - len(selected)]
                    )
                    skipped += count
                    if len(selected) >= limit:
                        break
                return {"games": selected, "total": total, "offset": offset}
            if path == "/api/replay":
                return await self.replay(
                    first("id"), int(first("offset", "0")), int(first("limit", "40"))
                )
            if path == "/api/map":
                board = await asyncio.to_thread(
                    parse_map, self.maps.path(first("id")).read_text(encoding="utf-8-sig")
                )
                return geometry(board)
        elif method == "POST":
            if not isinstance(body, dict):
                raise ValueError("A JSON object is required.")
            if path == "/api/review":
                return await self.review(body)
            if path == "/api/approve":
                return await self.approve(str(body.get("approval", "")))
            if path == "/api/account":
                return await self.connect(body)
            if path == "/api/refresh":
                self.auth_failed = False
                await self.refresh_data(force=True)
                return await self.state()
            if path == "/api/stop":
                if self.runner:
                    self.runner.cancel.set()
                return {"stopping": True}
            if path == "/api/import-replay":
                entry, _ = await asyncio.to_thread(
                    self.library.import_file, str(body.get("path", ""))
                )
                return {"entry": entry}
            if path == "/api/online-replay":
                api = self.api
                await self.guard_account(api)
                battle_id = identifier(body.get("id"))
                if not any(
                    identifier(battle_row(row).get("id")) == battle_id for row in self.battles
                ):
                    raise ValueError("Select one of your battles.")
                detail = await api.get(f"/battles/{battle_id}")
                await self.guard_account(api)
                games = rows(detail, "games")
                if not games and isinstance(detail, dict) and isinstance(detail.get("match"), dict):
                    games = rows(detail["match"], "games")
                if not games:
                    raise ValueError("This battle does not have a saved game replay yet.")
                game_id = identifier(games[0].get("id"))
                destination = self.staging / f"online-{game_id}.replay"
                await api.download(f"/battles/{game_id}/replay", destination, max_bytes=MAX_FILE)
                await self.guard_account(api)
                entry, _ = await asyncio.to_thread(self.library.import_file, destination)
                return {"replay": f"library:{entry['id']}"}
        raise ValueError("Unknown dashboard endpoint.")

    async def close(self) -> None:
        self.closed = True
        if self.runner:
            self.runner.cancel.set()
        self.install_cancel.set()
        tasks = [task for task in (self.run_task, self.install_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.api.close()
        self.temporary.cleanup()


class LocalServer:
    """No LAN binding, CORS, arbitrary asset paths, key-bearing URLs or request logs."""

    def __init__(self, backend: Dashboard, port: int = 0):
        self.backend = backend
        self.cookie = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def headers_ok(self, *, api: bool = False, write: bool = False) -> bool:
                if self.headers.get_all("Host") != [outer.host]:
                    return False
                origins = self.headers.get_all("Origin")
                if origins and origins != [outer.url]:
                    return False
                if api:
                    if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                        return False
                    try:
                        cookies = SimpleCookie(self.headers.get("Cookie", ""))
                        session = cookies.get("battlecode_session")
                        if session is None or not secrets.compare_digest(
                            session.value, outer.cookie
                        ):
                            return False
                    except Exception:
                        return False
                if write:
                    if origins != [outer.url] or not secrets.compare_digest(
                        self.headers.get("X-Battlecode-CSRF", ""), outer.csrf
                    ):
                        return False
                return True

            def respond(
                self, data: bytes, content_type: str, status: int = 200, *, cookie: bool = False
            ):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
                )
                self.send_header("X-Frame-Options", "DENY")
                if cookie:
                    self.send_header(
                        "Set-Cookie",
                        f"battlecode_session={outer.cookie}; Path=/; HttpOnly; SameSite=Strict",
                    )
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def json_response(self, value, status=200):
                token = outer.backend.api.credential.token if outer.backend.api.credential else ""
                encoded = redact(
                    json.dumps(value, ensure_ascii=True, allow_nan=False), token
                ).encode()
                self.respond(encoded, "application/json; charset=utf-8", status)

            def forbidden(self):
                self.json_response(
                    {"error": "This dashboard only accepts its own local browser session."}, 403
                )

            def do_GET(self):
                path = urlsplit(self.path).path
                if path.startswith("/api/"):
                    if not self.headers_ok(api=True):
                        return self.forbidden()
                    self.call_backend("GET")
                    return
                if not self.headers_ok():
                    return self.forbidden()
                name = (
                    "index.html"
                    if path == "/"
                    else path.removeprefix("/assets/")
                    if path.startswith("/assets/")
                    else ""
                )
                if (
                    not re.fullmatch(r"[a-zA-Z0-9_-]+\.(html|css|js|png|woff2)", name)
                    or not ASSETS.joinpath(name).is_file()
                ):
                    return self.json_response({"error": "Not found."}, 404)
                mime = (
                    {".js": "text/javascript", ".woff2": "font/woff2"}.get(Path(name).suffix)
                    or mimetypes.guess_type(name)[0]
                    or "application/octet-stream"
                )
                self.respond(ASSETS.joinpath(name).read_bytes(), mime, cookie=path == "/")

            def do_POST(self):
                if not self.headers_ok(api=True, write=True):
                    return self.forbidden()
                try:
                    if len(self.headers.get_all("Content-Length", [])) != 1:
                        raise ValueError("Exactly one content length is required.")
                    length = int(self.headers.get("Content-Length", "0"))
                    upload = urlsplit(self.path).path == "/api/file"
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= (
                        MAX_IMPORT_BYTES if upload else 65536
                    ):
                        return self.json_response({"error": "Request exceeds the size limit."}, 413)
                    self.connection.settimeout(30)
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("Incomplete request.")
                    if upload:
                        if self.headers.get("Content-Type") != "application/octet-stream":
                            raise ValueError("Use a binary file upload.")
                        query = parse_qs(urlsplit(self.path).query)
                        coroutine = outer.backend.stage_file(
                            query.get("kind", [""])[0],
                            unquote(self.headers.get("X-File-Name", "file")),
                            raw,
                        )
                        self.await_result(coroutine)
                    else:
                        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                            raise ValueError("Use a JSON request.")
                        self.call_backend("POST", json.loads(raw))
                except (ValueError, OSError) as error:
                    self.json_response({"error": clean_label(error, 500)}, 400)

            def await_result(self, coroutine):
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, outer.loop)
                    result = future.result(timeout=120)
                    self.json_response({**result, "csrf": outer.csrf})
                except (APIError, ValueError, OSError) as error:
                    self.json_response(
                        {"error": clean_label(error, 500)}, getattr(error, "status", 0) or 400
                    )
                except TimeoutError:
                    # Never resubmit a mutation: it may already have reached the remote server.
                    self.json_response(
                        {
                            "error": "Request still pending. Refresh before trying again; it was not retried."
                        },
                        504,
                    )
                except Exception:
                    self.json_response(
                        {
                            "error": "Could not complete this action. Refresh and inspect its state before trying again."
                        },
                        500,
                    )

            def call_backend(self, method, body=None):
                self.await_result(outer.backend.dispatch(method, self.path, body))

        self.http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.http.daemon_threads = True
        self.host = f"127.0.0.1:{self.http.server_port}"
        self.url = f"http://{self.host}"
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def start(self) -> None:
        self.loop_thread.start()
        self.http_thread.start()

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()
        self.http_thread.join(timeout=3)
        try:
            asyncio.run_coroutine_threadsafe(self.backend.close(), self.loop).result(timeout=20)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.loop_thread.join(timeout=3)
            self.loop.close()


def serve(
    *, demo: bool = False, port: int = 0, open_browser: bool = True, refresh: int = 45
) -> None:
    backend = Dashboard(demo=demo, refresh=refresh)
    try:
        server = LocalServer(backend, port)
    except OSError:
        asyncio.run(backend.close())
        raise
    server.start()
    print(
        f"Battlecode dashboard: {server.url}\nLocal only. Ctrl+C stops the dashboard and any running simulation.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(server.url)
    try:
        while server.http_thread.is_alive():
            time.sleep(0.5)
    finally:
        server.close()
