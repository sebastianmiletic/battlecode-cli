"""Local, reproducible benchmarks through the official unswbc judge sandbox.

No account writes, shell strings, native bot execution, or custom-map server uploads.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath

from filelock import FileLock

from .bots import PreparedBot, prepare_bot
from .config import atomic_write, data_dir, redact
from .replays import ReplayLibrary, clean_label, load_replay, parse_map, path_value

MAX_MAPS = 1000
MAX_MAP_BYTES = 1024 * 1024
MAX_IMPORT_BYTES = 128 * 1024 * 1024
MAX_GAMES = 10_000
MAX_OPPONENTS = 20
MAX_LOG_BYTES = 2 * 1024 * 1024


class ArenaError(ValueError):
    pass


def runner_path() -> str | None:
    found = shutil.which("unswbc")
    if found:
        return found
    # Also support launching battlecode-cli by its full path before PATH has refreshed.
    root = Path(os.environ.get("UV_TOOL_BIN_DIR", str(Path.home() / ".local" / "bin")))
    candidate = root / ("unswbc.exe" if os.name == "nt" else "unswbc")
    return (
        str(candidate)
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK))
        else None
    )


def runner_environment() -> dict[str, str]:
    """Runner processes get OS/toolchain essentials, not the agent's credential environment."""
    allowed = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LOCALAPPDATA",
        "APPDATA",
        "XDG_CACHE_HOME",
        "LANG",
        "LC_ALL",
        "COMSPEC",
        "PROCESSOR_ARCHITECTURE",
        "PATHEXT",
        "NUMBER_OF_PROCESSORS",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(TERM="dumb", NO_COLOR="1", UNSWBC_NO_UPDATE="1", PYTHONUTF8="1")
    return result


class MapLibrary:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else data_dir() / "arena" / "maps"

    def entries(self) -> list[dict]:
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.glob("*.json"))[: MAX_MAPS + 1]:
            try:
                if path.stat().st_size > 8192:
                    raise ValueError
                entry = json.loads(path.read_text(encoding="utf-8"))
                if entry.get("id") != path.stem or not re.fullmatch(r"[a-f0-9]{24}", path.stem):
                    raise ValueError
                if self.path(path.stem).is_file():
                    result.append(entry)
            except (ValueError, OSError, AttributeError) as error:
                raise ArenaError(f"Unreadable map metadata: {path.name}") from error
        if len(result) > MAX_MAPS:
            raise ArenaError("Map library exceeds its 1,000-map limit.")
        return sorted(result, key=lambda entry: (entry["name"].casefold(), entry["id"]))

    def ensure_official(self) -> dict:
        """Ship all 15 unchanged maps from unswbc 1.2.2, including offline installs."""
        resources = files("battlecode_cli").joinpath("assets/maps")
        return self.import_blobs(
            [
                (entry.name, entry.read_bytes())
                for entry in resources.iterdir()
                if entry.name.endswith(".map")
            ],
            origin="official",
        )

    def path(self, ident: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise ArenaError("Invalid local map ID.")
        return self.root / f"{ident}.map"

    def import_blobs(self, blobs: list[tuple[str, bytes]], *, origin: str = "custom") -> dict:
        if len(blobs) > MAX_MAPS or sum(len(raw) for _, raw in blobs) > MAX_IMPORT_BYTES:
            raise ArenaError("Import at most 1,000 maps and 128 MB at a time.")
        valid, rejected = {}, []
        for name, raw in blobs:
            try:
                if not raw or len(raw) > MAX_MAP_BYTES:
                    raise ArenaError("Map is empty or exceeds 1 MB.")
                parsed = parse_map(raw.decode("utf-8-sig"))
                if None in parsed.queens:
                    raise ArenaError("Map needs starting dragons for both teams.")
                ident = hashlib.sha256(raw).hexdigest()[:24]
                valid[ident] = (
                    {
                        "id": ident,
                        "name": clean_label(
                            parsed.name if parsed.name != "Unnamed map" else Path(name).stem
                        ),
                        "width": parsed.width,
                        "height": parsed.height,
                        "origins": [origin],
                    },
                    raw,
                )
            except (ValueError, UnicodeError) as error:
                rejected.append(f"{clean_label(name)}: {clean_label(error)}")
        if not valid:
            raise ArenaError(rejected[0] if rejected else "No .map or .txt maps found.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        added = 0
        with FileLock(str(self.root / ".lock")):
            existing = {entry["id"]: entry for entry in self.entries()}
            if len(set(existing) | set(valid)) > MAX_MAPS:
                raise ArenaError("Map library is limited to 1,000 unique maps.")
            for ident, (entry, raw) in valid.items():
                if ident in existing:
                    entry["origins"] = sorted(set(existing[ident].get("origins", [])) | {origin})
                else:
                    added += 1
                atomic_write(self.path(ident), raw)
                atomic_write(self.root / f"{ident}.json", json.dumps(entry).encode())
        return {
            "added": added,
            "duplicates": len(blobs) - len(rejected) - added,
            "rejected": rejected,
        }

    def import_path(self, value: str, *, origin: str = "custom") -> dict:
        source = path_value(value)
        blobs = []
        total = 0

        def add(name: str, raw: bytes):
            nonlocal total
            total += len(raw)
            if len(blobs) >= MAX_MAPS or total > MAX_IMPORT_BYTES:
                raise ArenaError("Import at most 1,000 maps and 128 MB at a time.")
            blobs.append((name, raw))

        if source.is_dir():
            visited = 0
            for root, directories, filenames in os.walk(source, followlinks=False):
                directories[:] = [
                    d
                    for d in sorted(directories)
                    if not d.startswith(".") and not (Path(root) / d).is_symlink()
                ]
                visited += len(directories) + len(filenames)
                if visited > 20_000:
                    raise ArenaError("Choose a smaller maps folder (at most 20,000 entries).")
                for name in sorted(filenames):
                    path = Path(root) / name
                    if path.suffix.lower() not in (".map", ".txt") or path.is_symlink():
                        continue
                    if path.stat().st_size > MAX_MAP_BYTES:
                        raise ArenaError(f"{clean_label(name)} exceeds 1 MB.")
                    with path.open("rb") as handle:
                        add(str(path.relative_to(source)), handle.read(MAX_MAP_BYTES + 1))
        elif source.is_file() and source.suffix.lower() == ".zip":
            if source.stat().st_size > MAX_IMPORT_BYTES:
                raise ArenaError("Map ZIP exceeds 128 MB.")
            try:
                with zipfile.ZipFile(source) as archive:
                    items = archive.infolist()
                    if len(items) > 5000:
                        raise ArenaError("Map ZIP contains too many entries.")
                    for item in items:
                        path = PurePosixPath(item.orig_filename)
                        if (
                            item.orig_filename != item.filename
                            or path.is_absolute()
                            or ".." in path.parts
                            or "\\" in item.orig_filename
                            or ":" in item.orig_filename
                            or any(ord(c) < 32 for c in item.orig_filename)
                            or stat.S_ISLNK(item.external_attr >> 16)
                            or item.flag_bits & 1
                        ):
                            raise ArenaError(
                                "Map ZIP contains an unsafe, linked or encrypted entry."
                            )
                        if item.is_dir() or path.suffix.lower() not in (".map", ".txt"):
                            continue
                        if (
                            item.file_size > MAX_MAP_BYTES
                            or total + item.file_size > MAX_IMPORT_BYTES
                        ):
                            raise ArenaError("Map ZIP exceeds decompression limits.")
                        add(item.filename, archive.read(item))
            except (zipfile.BadZipFile, RuntimeError) as error:
                raise ArenaError("Map ZIP is corrupt or unreadable.") from error
        elif source.is_file() and source.suffix.lower() in (".map", ".txt"):
            if source.stat().st_size > MAX_MAP_BYTES:
                raise ArenaError("Map exceeds 1 MB.")
            with source.open("rb") as handle:
                add(source.name, handle.read(MAX_MAP_BYTES + 1))
        else:
            raise ArenaError("Choose a maps folder, ZIP, .map or .txt file.")
        return self.import_blobs(blobs, origin=origin)


@dataclass(frozen=True)
class BotVersion:
    label: str
    prepared: PreparedBot


def bot_version(value: str) -> BotVersion:
    prepared = prepare_bot(value)
    return BotVersion(clean_label(prepared.path.stem), prepared)


def opponent_versions(value: str) -> list[BotVersion]:
    if not value.strip():
        return []
    root = path_value(value)
    if not root.is_dir():
        raise ArenaError("Opponents must be a folder of bot ZIPs or project folders.")
    candidates = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or path.name.startswith("."):
            continue
        if (path.is_file() and path.suffix.lower() == ".zip") or (
            path.is_dir() and (path / "bot.toml").is_file()
        ):
            candidates.append(path)
        if len(candidates) > MAX_OPPONENTS:
            raise ArenaError("Choose at most 20 local opponents.")
    if not candidates:
        raise ArenaError("No opponent bot ZIPs or projects found in this folder.")
    return [bot_version(str(path)) for path in candidates]


@dataclass(frozen=True)
class ArenaPlan:
    bots: tuple[BotVersion, ...]
    maps: tuple[dict, ...]
    repeats: int = 1
    swap: bool = True
    seed: int = 0
    timeout: int = 600

    @property
    def pairs(self) -> list[tuple[int, int]]:
        return [(0, 1)] + [
            (candidate, other) for other in range(2, len(self.bots)) for candidate in (0, 1)
        ]

    @property
    def count(self) -> int:
        return len(self.pairs) * len(self.maps) * self.repeats * (2 if self.swap else 1)

    def validate(self) -> None:
        if not 2 <= len(self.bots) <= MAX_OPPONENTS + 2 or not self.maps:
            raise ArenaError("Choose two bot versions and at least one map.")
        if (
            not 1 <= self.repeats <= 10
            or not 0 <= self.seed < 2**64
            or not 30 <= self.timeout <= 3600
        ):
            raise ArenaError("Use 1–10 repeats, a 64-bit seed and a 30–3,600 second timeout.")
        if self.count > MAX_GAMES:
            raise ArenaError("Reduce maps, opponents or repeats: maximum 10,000 games per batch.")


def snapshot_bot(bot: BotVersion, destination: Path) -> Path:
    destination.mkdir(parents=True, mode=0o700)
    with zipfile.ZipFile(io.BytesIO(bot.prepared.blob)) as archive:
        for name in bot.prepared.files:
            if name == "bot.toml":
                continue
            target = destination.joinpath(*PurePosixPath(name).parts)
            atomic_write(target, archive.read(name))
    # Canonical project prevents a user-provided output path escaping the private workspace.
    atomic_write(
        destination / "bot.toml",
        f'[project]\nlanguage="{bot.prepared.language}"\ninclude=["*"]\noutput=".arena-build"\n'.encode(),
    )
    if bot.prepared.language == "python" and not (destination / "main.py").is_file():
        raise ArenaError(f"{bot.label}: Python sandbox projects need main.py at the root.")
    if bot.prepared.language != "python":
        if (destination / "main.py").exists():
            raise ArenaError(f"{bot.label}: remove root main.py from a C/C++ sandbox archive.")
        if not any(
            PurePosixPath(name).suffix in (".c", ".cc", ".cpp", ".cxx", ".c++")
            and all(not part.startswith(".") for part in PurePosixPath(name).parts)
            for name in bot.prepared.files
        ):
            raise ArenaError(f"{bot.label}: no visible C/C++ compilation units found.")
    return destination


class ArenaStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else data_dir() / "arena" / "runs"

    def path(self, ident: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise ArenaError("Invalid benchmark ID.")
        return self.root / ident

    def save(self, job: dict) -> None:
        root = self.path(job["id"])
        atomic_write(root / "results.json", json.dumps(job).encode())
        header = {key: job[key] for key in ("id", "created_at", "status", "total", "bots", "seed")}
        header["completed"] = len(job["games"])
        atomic_write(root / "summary.json", json.dumps(header).encode())

    def load(self, ident: str) -> dict:
        path = self.path(ident) / "results.json"
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ArenaError("Benchmark results exceed the safety limit.")
        job = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(job, dict)
            or job.get("id") != ident
            or not isinstance(job.get("games"), list)
        ):
            raise ArenaError("Invalid benchmark results.")
        if job.get("status") == "running":
            job["status"] = "interrupted"
        return job

    def entries(self) -> list[dict]:
        if not self.root.exists():
            return []
        result = []
        for path in self.root.iterdir():
            if path.is_dir() and re.fullmatch(r"[a-f0-9]{32}", path.name):
                try:
                    summary = path / "summary.json"
                    if summary.exists() and summary.stat().st_size <= 16384:
                        entry = json.loads(summary.read_text(encoding="utf-8"))
                        if isinstance(entry, dict) and entry.get("id") == path.name:
                            result.append(entry)
                    else:
                        job = self.load(path.name)
                        result.append(
                            {
                                **{
                                    key: job[key]
                                    for key in (
                                        "id",
                                        "created_at",
                                        "status",
                                        "total",
                                        "bots",
                                        "seed",
                                    )
                                },
                                "completed": len(job["games"]),
                            }
                        )
                except (ValueError, OSError):
                    continue
        return sorted(result, key=lambda job: job.get("created_at", ""), reverse=True)[:100]


async def terminate(process) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        process.kill()
    await process.wait()


async def run_process(
    argv: list[str], *, cwd: Path, timeout: int, cancel: asyncio.Event
) -> tuple[int, str]:
    options = (
        {"start_new_session": True}
        if os.name == "posix"
        else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {}
    )
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=runner_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **options,
    )

    async def capture():
        output = bytearray()
        while chunk := await process.stdout.read(65536):
            if len(output) < MAX_LOG_BYTES:
                output.extend(chunk[: MAX_LOG_BYTES - len(output)])
        await process.wait()
        return bytes(output)

    reader = asyncio.create_task(capture())
    stopper = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {reader, stopper}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if reader not in done:
            await terminate(process)
            await reader
            if cancel.is_set():
                raise asyncio.CancelledError
            raise ArenaError(f"Game timed out after {timeout}s. Its process tree was stopped.")
        return process.returncode, redact(reader.result().decode("utf-8", "replace"))
    except asyncio.CancelledError:
        await terminate(process)
        await reader
        raise
    finally:
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)


class ArenaRunner:
    def __init__(
        self,
        store: ArenaStore,
        maps: MapLibrary,
        command: list[str] | None = None,
        library: ReplayLibrary | None = None,
    ):
        self.store, self.maps = store, maps
        self.library = library if library is not None else ReplayLibrary()
        self.command = command
        self.cancel = asyncio.Event()

    async def run(self, plan: ArenaPlan, progress: Callable[[dict], None] | None = None) -> dict:
        plan.validate()
        command = self.command or ([runner_path()] if runner_path() else [])
        if not command:
            raise ArenaError("Install the official runner: uv tool install --python 3.13 unswbc")
        ident = uuid.uuid4().hex
        root = self.store.path(ident)
        root.mkdir(parents=True, mode=0o700)
        paths = await asyncio.to_thread(
            lambda: [snapshot_bot(bot, root / f"bot-{i}") for i, bot in enumerate(plan.bots)]
        )
        map_paths = {}
        for entry in plan.maps:
            path = root / "maps" / f"{entry['id']}.map"
            raw = self.maps.path(entry["id"]).read_bytes()
            if len(raw) > MAX_MAP_BYTES or hashlib.sha256(raw).hexdigest()[:24] != entry["id"]:
                raise ArenaError(
                    "A selected map changed. Reimport it and review the benchmark again."
                )
            atomic_write(path, raw)
            map_paths[entry["id"]] = path
        job = {
            "id": ident,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "mode": "Simulation",
            "total": plan.count,
            "bots": [
                {"label": f"{i + 1}: {bot.label}", "sha256": bot.prepared.digest}
                for i, bot in enumerate(plan.bots)
            ],
            "maps": list(plan.maps),
            "seed": plan.seed,
            "repeats": plan.repeats,
            "swap": plan.swap,
            "games": [],
            "current": "Preparing sandbox",
        }

        async def update():
            await asyncio.to_thread(self.store.save, job)
            if progress:
                progress(job)

        await update()
        try:
            for left, right in plan.pairs:
                for entry in plan.maps:
                    for repeat in range(plan.repeats):
                        for swapped in range(2 if plan.swap else 1):
                            if self.cancel.is_set():
                                raise asyncio.CancelledError
                            if shutil.disk_usage(root).free < 256 * 1024 * 1024:
                                raise ArenaError(
                                    "Less than 256 MB free. Stopped before writing more replays."
                                )
                            a, b = (right, left) if swapped else (left, right)
                            index = len(job["games"])
                            replay_path = root / f"game-{index:05}.replay"
                            seed = (plan.seed + repeat) % 2**64
                            game = {
                                "index": index,
                                "mode": "Simulation",
                                "map": entry["name"],
                                "map_id": entry["id"],
                                "a": a,
                                "b": b,
                                "seed": seed,
                                "replay": replay_path.name,
                                "error": None,
                            }
                            job["current"] = (
                                f"{entry['name']} · {job['bots'][a]['label']} vs {job['bots'][b]['label']} · seed {seed}"
                            )
                            await update()
                            started = time.monotonic()
                            try:
                                code, output = await run_process(
                                    [
                                        *command,
                                        "run",
                                        str(map_paths[entry["id"]]),
                                        str(paths[a]),
                                        str(paths[b]),
                                        "--sandbox",
                                        "--seed",
                                        str(seed),
                                        "--no-logs",
                                        "--no-draw",
                                        "--no-indicator",
                                        "-o",
                                        str(replay_path),
                                    ],
                                    cwd=root,
                                    timeout=plan.timeout,
                                    cancel=self.cancel,
                                )
                                atomic_write(root / f"game-{index:05}.log", output.encode())
                                if code != 0:
                                    raise ArenaError(
                                        output[-1600:] or f"Runner exited with code {code}."
                                    )
                                replay = await asyncio.to_thread(load_replay, replay_path)
                                if replay.end_reason == "Unfinished replay":
                                    raise ArenaError(
                                        "Runner saved an unfinished replay; result excluded."
                                    )
                                replay = replace(
                                    replay, bots=(job["bots"][a]["label"], job["bots"][b]["label"])
                                )
                                game["summary"] = replay.summary()
                                game["diagnostics"] = replay.diagnostics
                                try:
                                    saved, _ = await asyncio.to_thread(
                                        self.library.import_file,
                                        replay_path,
                                        mode="Simulation",
                                        bot_names=replay.bots,
                                    )
                                    game["library_id"] = saved["id"]
                                except (ValueError, OSError) as error:
                                    # The original run replay is already saved even if the library is full.
                                    game["library_error"] = redact(str(error))[:400]
                                game["map_corrections"] = bool(
                                    re.search(r"map: \d+ defects? found", output)
                                )
                            except (ValueError, OSError) as error:
                                game["error"] = redact(str(error))[:1600]
                            game["seconds"] = round(time.monotonic() - started, 2)
                            job["games"].append(game)
                            await update()
            job["status"] = "complete"
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        except (ValueError, OSError) as error:
            job["status"] = "stopped"
            job["error"] = redact(str(error))
        finally:
            job["current"] = ""
            await update()
        return job


def aggregates(job: dict) -> list[dict]:
    result = [
        {
            "label": bot["label"],
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "rounds": 0,
            "moves": 0,
            "deaths": {},
            "growth": 0,
            "splits": 0,
            "actions": 0,
            "sprints": 0,
            "requested_steps": 0,
            "queen_deaths": 0,
        }
        for bot in job["bots"]
    ]
    for game in job["games"]:
        if game.get("error") or not game.get("summary"):
            continue
        winner = game["summary"]["winner"]
        for side, index in (("A", game["a"]), ("B", game["b"])):
            row = result[index]
            row["draws" if winner is None else "wins" if winner == side else "losses"] += 1
            row["rounds"] += game["summary"]["rounds"]
            metrics = game.get("diagnostics", {}).get(side, {})
            for field in ("moves", "growth", "splits", "actions", "sprints", "requested_steps"):
                row[field] += metrics.get(field, 0)
            row["queen_deaths"] += metrics.get("queen_death_round") is not None
            for reason, count in metrics.get("deaths", {}).items():
                row["deaths"][reason] = row["deaths"].get(reason, 0) + count
    return result


def report(job: dict) -> str:
    from .models import record_label, winrate

    parts = [
        f"Simulation {job['id']}",
        f"{job['status'].upper()} · {len(job['games'])}/{job['total']} games · base seed {job['seed']}",
        "",
        "BOT COMPARISON",
    ]
    for row in aggregates(job):
        total = row["wins"] + row["draws"] + row["losses"]
        parts.extend(
            [
                f"{row['label']}: {record_label(row)} · {winrate(row)} wins",
                f"  {row['moves']:,} recorded moves · +{row['growth']:,} growth units · {row['splits']:,} splits · {row['rounds'] / total if total else 0:.1f} mean rounds",
            ]
        )
        if any(game.get("diagnostics", {}).get("command_events") for game in job["games"]):
            parts.append(
                f"  {row['actions']:,} commands · {row['requested_steps']:,} requested steps · {row['sprints']:,} sprints · {row['queen_deaths']} queen losses"
            )
        for reason, count in sorted(row["deaths"].items(), key=lambda pair: -pair[1]):
            parts.append(f"  {reason}: {count}")
        if row["deaths"].get("hit wall"):
            parts.append(
                "  Check wall avoidance and wrap/portal transitions in the losing replays."
            )
        if row["deaths"].get("hit self"):
            parts.append("  Check tail-vacating assumptions and growth-aware path safety.")
        if row["deaths"].get("hit body"):
            parts.append("  Check friendly-dragon coordination and occupied-cell prediction.")
        if row["deaths"].get("head-to-head"):
            parts.append("  Check contested-cell timing, sprint commitment and length comparisons.")
        if row["deaths"].get("invalid action"):
            parts.append(
                "  Check command validity and CPU budgets; invalid-action deaths do not distinguish these causes."
            )
    parts.extend(["", "PER-MAP RECORDS (same seeds, both seats when enabled)"])
    for entry in job["maps"]:
        scoped = dict(job, games=[g for g in job["games"] if g["map_id"] == entry["id"]])
        parts.append(entry["name"])
        parts.extend(
            f"  {row['label']}: {record_label(row)} · {winrate(row)}" for row in aggregates(scoped)
        )
    losses = [
        game
        for game in job["games"]
        if not game.get("error") and game.get("summary", {}).get("winner") in ("A", "B")
    ]
    if losses:
        parts.extend(["", "FAILURE NOTES (first 50 decided games)"])
        for game in losses[:50]:
            side = "B" if game["summary"]["winner"] == "A" else "A"
            index = game["a"] if side == "A" else game["b"]
            metrics = game.get("diagnostics", {}).get(side, {})
            final = game["summary"].get("final", {}).get(side, {})
            parts.append(
                f"  Game {game['index'] + 1} · {game['map']} · {job['bots'][index]['label']} lost · {game['summary']['end_reason']}"
            )
            parts.append(
                f"    Queen death round: {metrics.get('queen_death_round')} · final total length: {final.get('total', 'n/a')} · moves: {metrics.get('moves', 0)} · sprints: {metrics.get('sprints', 0)}"
            )
    errors = [game for game in job["games"] if game.get("error")]
    if errors:
        parts.extend(["", f"ERRORS ({len(errors)}; excluded from win rates)"])
        parts.extend(f"  Game {game['index'] + 1}: {game['error']}" for game in errors[:20])
    if any(game.get("map_corrections") for game in job["games"]):
        parts.extend(
            ["", "The engine corrected defects in some maps. Check the saved runner logs."]
        )
    parts.extend(
        [
            "",
            "Diagnostics count every recorded movement, growth, split and death event, not sampled frames.",
            "They identify observed failure modes, not causal strategy analysis or AI recommendations.",
            "Local results do not change server ELO. Other teams' private bot source is not downloadable.",
        ]
    )
    return "\n".join(parts)
