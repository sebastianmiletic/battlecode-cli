"""Server rating series, clearly inferred ranks, and local observed snapshots."""

from __future__ import annotations

import bisect
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock

from .config import atomic_write, data_dir

MAX_POINTS = 3000


def number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def timestamp(value) -> str | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def series(history, field: str) -> list[tuple[str, float]]:
    if not isinstance(history, list):
        return []
    result = {}
    for point in history[-MAX_POINTS:]:
        if not isinstance(point, dict):
            continue
        date = timestamp(point.get("date", point.get("at")))
        value = number(point.get(field, point.get("rating") if field == "elo" else None))
        if date and value is not None:
            result[date] = value
    return sorted(result.items())


def histories(team: dict, standings: list[dict]) -> tuple[list, list, bool]:
    """Rank inferred from historical ELO uses today's eligible teams, not past eligibility."""
    ident = team.get("id", team.get("teamId"))
    ours = next((t for t in standings if t.get("id", t.get("teamId")) == ident), team)
    history = ours.get("history", ours.get("ratingHistory", team.get("history", [])))
    elo = series(history, "elo")
    ranks = series(history, "rank") or series(ours.get("rankHistory", []), "rank")
    if not ranks and (ours.get("ranked") is False or ours.get("eligible") is False):
        return elo, [], False
    if ranks or not elo:
        return elo, ranks, False
    others = []
    for entry in standings[:5000]:
        if entry is ours or entry.get("ranked") is False or entry.get("eligible") is False:
            continue
        points = series(entry.get("history", entry.get("ratingHistory", [])), "elo")
        if points:
            others.append(([date for date, _ in points], [value for _, value in points]))
    if not others:
        return elo, [], False
    for date, rating in elo:
        ahead = 0
        for dates, values in others:
            index = bisect.bisect_right(dates, date) - 1
            if index >= 0 and values[index] > rating:
                ahead += 1
        ranks.append((date, float(ahead + 1)))
    return elo, ranks, True


class HistoryStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else data_dir() / "history"

    def observe(self, team: dict, rank, *, date: str | None = None) -> list[dict]:
        ident = team.get("id", team.get("teamId"))
        if type(ident) is not int or ident <= 0:
            return []
        rating = number(team.get("elo", team.get("rating")))
        rank = number(rank)
        if rating is None and rank is None:
            return []
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / f"team-{ident}.json"
        with FileLock(str(self.root / ".lock")):
            points = []
            if path.exists() and path.stat().st_size <= 1024 * 1024:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        points = [p for p in raw[-MAX_POINTS:] if isinstance(p, dict)]
                except (ValueError, OSError):
                    pass
            point = {"date": date or datetime.now(UTC).isoformat(), "elo": rating, "rank": rank}
            # Keep endpoints of unchanged stretches without recording every polling tick.
            if len(points) >= 2 and all(
                p.get("elo") == rating and p.get("rank") == rank for p in points[-2:]
            ):
                points[-1] = point
            else:
                points.append(point)
            atomic_write(path, json.dumps(points[-MAX_POINTS:]).encode())
        return points[-MAX_POINTS:]
