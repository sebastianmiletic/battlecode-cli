"""Small defensive adapters for the toolkit's lists and nested battle responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def rows(value: Any, *keys: str) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return rows(value[key])
    return []


def record(item: dict) -> tuple[int, int, int]:
    source = item.get("record") if isinstance(item.get("record"), dict) else item
    return tuple(int(source.get(key) or 0) for key in ("wins", "draws", "losses"))


def winrate(item: dict) -> str:
    source = item.get("record") if isinstance(item.get("record"), dict) else item
    if not all(key in source for key in ("wins", "draws", "losses")):
        return "n/a"
    wins, draws, losses = record(item)
    total = wins + draws + losses
    return f"{wins / total:.1%}" if total else "n/a"


def record_label(item: dict) -> str:
    wins, draws, losses = record(item)
    return f"{wins}W {draws}D {losses}L"


def date_label(value: Any) -> str:
    if not value:
        return "n/a"
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
        return date.strftime("%d %b %H:%M")
    except ValueError:
        return str(value)[:16]


def members_label(team: dict) -> str:
    members = team.get("members")
    if not isinstance(members, list):
        return "n/a"
    names = [
        str(member.get("username", member.get("name", "")))
        if isinstance(member, dict)
        else str(member)
        for member in members
    ]
    return ", ".join(name for name in names if name) or "n/a"


def leaderboard_rows(value: Any) -> list[dict]:
    result = []
    for entry in rows(value, "teams", "leaderboard", "ratings"):
        team = entry.get("team")
        merged = (
            {**team, **{key: val for key, val in entry.items() if key != "team"}}
            if isinstance(team, dict)
            else dict(entry)
        )
        ident = merged.get("id", merged.get("teamId"))
        if type(ident) is not int or ident <= 0:
            continue
        merged["id"] = ident
        result.append(merged)
    return sorted(
        result,
        key=lambda team: (team.get("rank") or 10**9, -(team.get("elo", team.get("rating")) or 0)),
    )


def team_data(value: dict) -> dict:
    return value.get("team") if isinstance(value.get("team"), dict) else value


def active_bot(submissions: list[dict]) -> dict | None:
    return next((s for s in submissions if s.get("status") == "active"), None)


def battle_row(value: dict, our_team_id: int | None = None) -> dict:
    match = value.get("match") if isinstance(value.get("match"), dict) else value
    side = "B" if our_team_id is not None and match.get("teamBId") == our_team_id else "A"
    opponent_side = "A" if side == "B" else "B"
    opponent = (
        value.get("opponent")
        or value.get(f"team{opponent_side}Name")
        or match.get(f"team{opponent_side}Name")
        or "Unknown team"
    )
    if isinstance(opponent, dict):
        opponent = opponent.get("name", "Unknown team")
    outcome = value.get("outcome")
    if not outcome:
        winner = str(match.get("winner") or "").upper()
        outcome = (
            "win"
            if winner == side
            else "loss"
            if winner in ("A", "B")
            else "draw"
            if winner in ("DRAW", "TIE")
            else match.get("status", "pending")
        )
    results = value.get("results")
    if isinstance(results, list):
        score = " ".join({"win": "W", "loss": "L", "draw": "D"}.get(str(r), ".") for r in results)
    else:
        score = str(
            value.get("mapName")
            or match.get("mapName")
            or ("5-game series" if match.get("ranked") else "Game")
        )
    change = value.get("eloChange", match.get("eloChange"))
    return {
        "id": value.get("id") or match.get("id"),
        "opponent": str(opponent),
        "mode": "Ranked" if match.get("ranked") else "Unranked",
        "result": str(outcome).upper(),
        "score": score,
        "change": f"{float(change):+g}" if isinstance(change, (int, float)) else "·",
        "at": date_label(value.get("at") or match.get("completedAt") or match.get("createdAt")),
    }
