"""Local replay decoding and library. Files never leave the machine."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from .capnp import DecodeError, List, Record, root_record
from .config import atomic_write, data_dir, redact

MAX_FILE = 64 * 1024 * 1024
MAX_FRAMES = 2001
MAX_DRAGONS = 20_000
MAX_BODY = 8192
MAX_SNAPSHOT_UNITS = 2_000_000
DEATH_REASONS = ("hit wall", "hit self", "hit body", "head-to-head", "invalid action")
Point = tuple[int, int]


class ReplayError(ValueError):
    pass


def clean_label(value: Any, limit: int = 160) -> str:
    return redact(" ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).split()))[:limit]


def path_value(value: str | Path) -> Path:
    return Path(str(value).strip().strip("\"'")).expanduser().resolve()


def read_bytes(path: Path) -> bytes:
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ReplayError("Choose a replay file no larger than 64 MB.")
    with path.open("rb") as handle:
        raw = handle.read(MAX_FILE + 1)
    if not raw or len(raw) > MAX_FILE:
        raise ReplayError("Replay is empty or exceeds the 64 MB safety limit.")
    return raw


@dataclass(frozen=True, slots=True)
class Dragon:
    id: int
    team: int
    body: tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class TeamStats:
    living: int
    longest: int
    total: int
    queen: int
    deaths: int
    splits: int


@dataclass(frozen=True, slots=True)
class Frame:
    round: int
    dragons: dict[int, Dragon]
    pearls: frozenset[Point]
    stats: tuple[TeamStats, TeamStats]
    events: tuple[str, ...]


@dataclass(frozen=True)
class ReplayMap:
    width: int
    height: int
    name: str
    horizontal: dict[Point, int]
    vertical: dict[Point, int]
    fountains: frozenset[Point]
    initial: tuple[Dragon, ...]
    queens: tuple[int | None, int | None]


@dataclass(frozen=True)
class Replay:
    map: ReplayMap
    bots: tuple[str, str]
    frames: tuple[Frame, ...]
    winner: str | None
    end_reason: str

    def summary(self) -> dict:
        final = self.frames[-1]
        return {
            "map": self.map.name,
            "width": self.map.width,
            "height": self.map.height,
            "bots": {"A": self.bots[0], "B": self.bots[1]},
            "rounds": len(self.frames) - 1,
            "frames": len(self.frames),
            "winner": self.winner,
            "end_reason": self.end_reason,
            "final": {
                side: {
                    "living": stats.living,
                    "longest": stats.longest,
                    "total": stats.total,
                    "queen": stats.queen,
                    "deaths": stats.deaths,
                    "splits": stats.splits,
                }
                for side, stats in zip(("A", "B"), final.stats, strict=True)
            },
        }


def integer(value: Any, low: int = 0, high: int = 2**31 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ReplayError("Replay contains an invalid integer or identifier.")
    return value


def parse_map(text: str) -> ReplayMap:
    width = height = 0
    name = "Unnamed map"
    horizontal: dict[Point, int] = {}
    vertical: dict[Point, int] = {}
    fountains: set[Point] = set()
    initial: list[Dragon] = []
    queens: list[int | None] = [None, None]
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        kind = parts[0]
        if kind == "MAP":
            if width or len(parts) != 3:
                raise ReplayError("Replay map header is invalid.")
            width, height = int(parts[1]), int(parts[2])
            if not 0 < width <= 512 or not 0 < height <= 512 or width * height > 65_536:
                raise ReplayError("Replay map exceeds the geometry safety limit.")
        elif kind == "MAP_NAME":
            name = clean_label(" ".join(parts[1:]))
        elif kind == "TILE":
            if not width or len(parts) != 5:
                raise ReplayError("Invalid replay tile.")
            x, y, minimum, maximum = map(int, parts[1:])
            integer(x, 0, width - 1)
            integer(y, 0, height - 1)
            if minimum == maximum == 1:
                fountains.add((x, y))
        elif kind == "EDGE":
            if not width or len(parts) < 3:
                raise ReplayError("Invalid replay edge.")
            ident, edge_type = int(parts[1]), int(parts[2])
            integer(ident, 0, (2 * height + 1) * (width + 1) - 1)
            integer(edge_type, 0, 2)
            row, col = divmod(ident, width + 1)
            if row % 2 == 0:
                integer(col, 0, width - 1)
                y = row // 2
                if edge_type:
                    horizontal[(col, y)] = edge_type
                    if y in (0, height):
                        horizontal[(col, height - y)] = edge_type
            elif edge_type:
                y = (row - 1) // 2
                vertical[(col, y)] = edge_type
                if col in (0, width):
                    vertical[(width - col, y)] = edge_type
        elif kind in ("DRAGON", "SNAKE"):
            if not width or len(initial) >= MAX_DRAGONS or len(parts) < 4:
                raise ReplayError("Invalid starting dragon.")
            team, length = int(parts[1]), int(parts[2])
            integer(team, 0, 1)
            integer(length, 1, MAX_BODY)
            if len(parts) != 3 + 2 * length:
                raise ReplayError("Starting dragon body is truncated.")
            body = tuple(
                (
                    integer(int(parts[3 + i * 2]), 0, width - 1),
                    integer(int(parts[4 + i * 2]), 0, height - 1),
                )
                for i in range(length)
            )
            ident = len(initial)
            initial.append(Dragon(ident, team, body))
            if queens[team] is None:
                queens[team] = ident
    if not width or not height or not initial:
        raise ReplayError("Replay has no valid map and starting dragons.")
    return ReplayMap(
        width,
        height,
        name,
        horizontal,
        vertical,
        frozenset(fountains),
        tuple(initial),
        (queens[0], queens[1]),
    )


class Builder:
    def __init__(self, replay_map: ReplayMap):
        self.map = replay_map
        self.dragons = {d.id: d for d in replay_map.initial}
        self.pearls: set[Point] = set()
        self.frames: list[Frame] = []
        self.round = -1
        self.started = False
        self.events: list[str] = []
        self.deaths = [0, 0]
        self.splits = [0, 0]
        self.snapshot_units = 0
        self.points: dict[Point, Point] = {}

    def point(self, value) -> Point:
        if isinstance(value, Record):
            raw = value.i32(0), value.i32(4)
        elif isinstance(value, dict):
            raw = value.get("x"), value.get("y")
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            raw = value[0], value[1]
        else:
            raise ReplayError("Replay point is malformed.")
        point = integer(raw[0], 0, self.map.width - 1), integer(raw[1], 0, self.map.height - 1)
        return self.points.setdefault(point, point)

    def body(self, value) -> tuple[Point, ...]:
        if isinstance(value, List):
            result = tuple(self.point(v) for v in value.records(MAX_BODY))
        elif isinstance(value, list) and len(value) <= MAX_BODY:
            result = tuple(self.point(v) for v in value)
        else:
            raise ReplayError("Replay body is malformed or too long.")
        if not result:
            raise ReplayError("Replay body is empty.")
        return result

    def snapshot(self) -> None:
        self.snapshot_units += sum(len(d.body) for d in self.dragons.values()) + len(self.pearls)
        if len(self.frames) >= MAX_FRAMES or self.snapshot_units > MAX_SNAPSHOT_UNITS:
            raise ReplayError("Replay exceeds the frame-state safety limit.")
        stats = []
        for team in (0, 1):
            lengths = [len(d.body) for d in self.dragons.values() if d.team == team]
            queen = self.dragons.get(self.map.queens[team])
            stats.append(
                TeamStats(
                    len(lengths),
                    max(lengths, default=0),
                    sum(lengths),
                    len(queen.body) if queen else 0,
                    self.deaths[team],
                    self.splits[team],
                )
            )
        self.frames.append(
            Frame(
                self.round,
                dict(self.dragons),
                frozenset(self.pearls),
                (stats[0], stats[1]),
                tuple(self.events[-1000:]),
            )
        )
        self.events.clear()

    def apply(self, kind: str, event: dict) -> None:
        if kind == "roundStart":
            next_round = integer(event.get("round"), 0, MAX_FRAMES - 2)
            if self.started and next_round <= self.round:
                raise ReplayError("Replay rounds are not in order.")
            self.snapshot()
            self.round = next_round
            self.started = True
        elif kind == "tileChange":
            point = self.point(event.get("tile"))
            if type(event.get("hasPearl")) is not bool:
                raise ReplayError("Replay pearl flag must be a boolean.")
            if event["hasPearl"]:
                self.pearls.add(point)
            else:
                self.pearls.discard(point)
        elif kind == "dragonUpdate":
            ident = integer(event.get("id"))
            dragon = self.dragons.get(ident)
            if dragon is None:
                raise ReplayError("Replay updates an unknown dragon.")
            head, tail = self.point(event.get("head")), self.point(event.get("tail"))
            body = (head, *dragon.body)
            try:
                last = len(body) - 1 - body[::-1].index(tail)
                body = body[: last + 1]
            except ValueError:
                body = body[:1]
            if len(body) > MAX_BODY:
                raise ReplayError("Replay dragon body exceeds the safety limit.")
            self.dragons[ident] = Dragon(ident, dragon.team, body)
        elif kind == "dragonSplit":
            parent_id, child_id = integer(event.get("parentId")), integer(event.get("childId"))
            team = integer(event.get("team"), 0, 1)
            parent = self.dragons.get(parent_id)
            if (
                parent is None
                or parent.team != team
                or child_id in self.dragons
                or len(self.dragons) >= MAX_DRAGONS
            ):
                raise ReplayError("Replay split is inconsistent.")
            self.dragons[parent_id] = Dragon(parent_id, team, self.body(event.get("parentBody")))
            self.dragons[child_id] = Dragon(child_id, team, self.body(event.get("childBody")))
            self.splits[team] += 1
            self.events.append(f"Team {'AB'[team]}: dragon #{parent_id} split, child #{child_id}.")
        elif kind == "dragonDeath":
            ident = integer(event.get("id"))
            dragon = self.dragons.pop(ident, None)
            if dragon is not None:
                self.deaths[dragon.team] += 1
                reason = event.get("reason", "unknown")
                if type(reason) is int:
                    reason = (
                        DEATH_REASONS[reason] if 0 <= reason < len(DEATH_REASONS) else "unknown"
                    )
                self.events.append(
                    f"Team {'AB'[dragon.team]}: dragon #{ident} died ({clean_label(reason, 50)}), length {len(dragon.body)}."
                )

    def finish(self) -> tuple[Frame, ...]:
        self.snapshot()
        return tuple(self.frames)


def _record(value) -> Record:
    if not isinstance(value, Record):
        raise ReplayError("Replay event payload is malformed.")
    return value


def decode_binary(raw: bytes) -> Replay:
    root = root_record(raw)
    replay_map = parse_map(root.text(0))
    bots = clean_label(root.text(1, 4096) or "Team A"), clean_label(root.text(2, 4096) or "Team B")
    events = root.pointer(3)
    if not isinstance(events, List):
        raise ReplayError("Replay event list is missing.")
    builder = Builder(replay_map)
    deadline = time.monotonic() + 30
    for index, event in enumerate(events.records()):
        if index % 4096 == 0 and time.monotonic() > deadline:
            raise ReplayError("Replay is too complex to decode within the safety limit.")
        kind = event.u16(0)
        if kind not in (0, 3, 9, 10, 11):
            continue
        value = _record(event.pointer(0))
        if kind == 0:
            builder.apply("roundStart", {"round": value.i32(0)})
        elif kind == 3:
            builder.apply(
                "tileChange",
                {"tile": value.pointer(0), "hasPearl": bool(value.number(0, "<B") & 1)},
            )
        elif kind == 9:
            builder.apply(
                "dragonUpdate",
                {"id": value.i32(0), "head": value.pointer(0), "tail": value.pointer(1)},
            )
        elif kind == 10:
            builder.apply(
                "dragonSplit",
                {
                    "parentId": value.i32(0),
                    "childId": value.i32(4),
                    "team": value.u16(8),
                    "parentBody": value.pointer(0),
                    "childBody": value.pointer(1),
                },
            )
        elif kind == 11:
            builder.apply("dragonDeath", {"id": value.i32(0), "reason": value.u16(4)})
    result = root.pointer(4)
    winner = None
    reason = "Unfinished replay"
    if isinstance(result, Record) and result.number(0, "<B") & 1:
        reason = {0: "Team eliminated", 1: "Round limit"}.get(result.u16(2), "Game ended")
        if result.u16(4) == 1:
            winner = "AB"[integer(result.u16(6), 0, 1)]
    return Replay(replay_map, bots, builder.finish(), winner, reason)


def decode_json(raw: bytes) -> Replay:
    if len(raw) > 8 * 1024 * 1024:
        raise ReplayError(
            "JSON replay exceeds the 8 MB safety limit. Use the official binary format for larger files."
        )
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ReplayError("Replay JSON is malformed.") from error
    if (
        not isinstance(data, dict)
        or data.get("format") != "battlecode-cli-replay"
        or data.get("version") != 1
    ):
        raise ReplayError("JSON must use the documented battlecode-cli-replay v1 format.")
    if not isinstance(data.get("map"), str) or len(data["map"]) > 2 * 1024 * 1024:
        raise ReplayError("Replay map text is missing or too large.")
    events = data.get("events")
    bots = data.get("bots", {})
    if not isinstance(events, list) or len(events) > 2_000_000 or not isinstance(bots, dict):
        raise ReplayError("Replay events or bot labels are invalid.")
    if any(
        not isinstance(bots.get(side, ""), str) or len(bots.get(side, "")) > 4096
        for side in ("A", "B")
    ):
        raise ReplayError("Replay bot labels must be short strings.")
    builder = Builder(parse_map(data["map"]))
    deadline = time.monotonic() + 30
    for index, event in enumerate(events):
        if index % 4096 == 0 and time.monotonic() > deadline:
            raise ReplayError("Replay is too complex to decode within the safety limit.")
        if not isinstance(event, dict) or event.get("type") not in (
            "roundStart",
            "tileChange",
            "dragonUpdate",
            "dragonSplit",
            "dragonDeath",
        ):
            raise ReplayError("Replay JSON contains an unsupported event.")
        builder.apply(event["type"], event)
    winner = data.get("winner")
    if winner not in (None, "A", "B"):
        raise ReplayError("Replay winner must be A, B or null.")
    return Replay(
        builder.map,
        (clean_label(bots.get("A", "Team A")), clean_label(bots.get("B", "Team B"))),
        builder.finish(),
        winner,
        clean_label(data.get("end_reason", "Imported replay")),
    )


def decode_replay(raw: bytes) -> Replay:
    if not raw or len(raw) > MAX_FILE:
        raise ReplayError("Replay is empty or exceeds the 64 MB safety limit.")
    try:
        if raw.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                raw = zipped.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                raise ReplayError("Gzipped replay exceeds the decompression safety limit.")
        if raw.lstrip().startswith(b"{"):
            return decode_json(raw)
        return decode_binary(raw)
    except (
        DecodeError,
        EOFError,
        UnicodeError,
        OSError,
        IndexError,
        KeyError,
        TypeError,
        RecursionError,
    ) as error:
        raise ReplayError("Replay is corrupt, truncated or uses an unsupported format.") from error


def load_replay(value: str | Path) -> Replay:
    return decode_replay(read_bytes(path_value(value)))


class ReplayLibrary:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else data_dir() / "replays"
        self.index = self.root / "library.json"

    def entries(self) -> list[dict]:
        if not self.index.exists():
            return []
        try:
            if self.index.stat().st_size > 12 * 1024 * 1024:
                raise ValueError
            data = json.loads(self.index.read_text(encoding="utf-8"))
            if (
                data.get("version") != 1
                or not isinstance(data.get("replays"), list)
                or len(data["replays"]) > 1000
            ):
                raise ValueError
            for entry in data["replays"]:
                if not isinstance(entry, dict) or not re.fullmatch(
                    r"[a-f0-9]{64}", entry.get("id", "")
                ):
                    raise ValueError
                if any(
                    not isinstance(entry.get(k), str) or len(entry[k]) > 200
                    for k in ("name", "map", "bot_a", "bot_b", "winner", "imported_at")
                ):
                    raise ValueError
                integer(entry.get("rounds"), 0, MAX_FRAMES - 1)
            if len({e["id"] for e in data["replays"]}) != len(data["replays"]):
                raise ValueError
            return data["replays"]
        except (ValueError, TypeError, AttributeError, OSError, RecursionError) as error:
            raise ReplayError(
                "Replay library index is unreadable. Restore library.json from a backup."
            ) from error

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ReplayError("Invalid local replay ID.")
        return self.root / f"{identifier}.replay"

    def import_file(self, value: str | Path) -> tuple[dict, Replay]:
        source = path_value(value)
        raw = read_bytes(source)
        replay = decode_replay(raw)
        digest = hashlib.sha256(raw).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.root / "library.lock", timeout=10):
            entries = self.entries()
            existing = next((e for e in entries if e["id"] == digest), None)
            if existing:
                # Reimport restores a lost library copy without creating a duplicate row.
                atomic_write(self.path(digest), raw)
                return existing, replay
            if len(entries) >= 1000:
                raise ReplayError("Library is full. Remove an old replay before importing another.")
            summary = replay.summary()
            entry = {
                "id": digest,
                "name": clean_label(source.name),
                "map": summary["map"],
                "bot_a": replay.bots[0],
                "bot_b": replay.bots[1],
                "rounds": summary["rounds"],
                "winner": replay.winner or "Draw / unfinished",
                "imported_at": datetime.now(UTC).isoformat(),
            }
            atomic_write(self.path(digest), raw)
            try:
                atomic_write(
                    self.index,
                    (
                        json.dumps({"version": 1, "replays": [entry, *entries]}, indent=2) + "\n"
                    ).encode(),
                )
            except OSError:
                self.path(digest).unlink(missing_ok=True)
                raise
        return entry, replay

    def remove(self, identifier: str) -> None:
        path = self.path(identifier)
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.root / "library.lock", timeout=10):
            entries = [e for e in self.entries() if e["id"] != identifier]
            atomic_write(
                self.index,
                (json.dumps({"version": 1, "replays": entries}, indent=2) + "\n").encode(),
            )
            path.unlink(missing_ok=True)
