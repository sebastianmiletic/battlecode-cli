"""Clearly labelled synthetic fixtures. Demo mode never contacts the server."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .api import APIError

TEAM = {
    "team": {"id": 900, "name": "Night shift", "elo": 1834, "wins": 128, "draws": 4, "losses": 72},
    "rank": 17,
    "peak": 1902,
}
TEAM["team"]["history"] = [
    {"date": f"2026-10-{1 + i // 24:02}T{i % 24:02}:00:00Z", "elo": value, "rank": rank}
    for i, (value, rank) in enumerate(
        zip(
            [
                1500,
                1520,
                1508,
                1544,
                1560,
                1580,
                1570,
                1630,
                1644,
                1628,
                1660,
                1685,
                1700,
                1680,
                1740,
                1760,
                1745,
                1770,
                1804,
                1790,
                1812,
                1840,
                1850,
                1830,
                1860,
                1902,
                1880,
                1860,
                1844,
                1834,
            ],
            [
                54,
                50,
                52,
                45,
                44,
                41,
                43,
                36,
                34,
                38,
                32,
                29,
                28,
                31,
                25,
                23,
                26,
                24,
                20,
                22,
                19,
                18,
                16,
                18,
                15,
                12,
                13,
                15,
                16,
                17,
            ],
            strict=True,
        )
    )
]
SUBMISSIONS = [
    {
        "id": 104,
        "version": 4,
        "name": "nightjar-v4",
        "status": "active",
        "language": "cpp",
        "wins": 49,
        "draws": 2,
        "losses": 19,
        "uploadedAt": "2026-10-02T03:30:00Z",
        "description": "Safer queen paths. Better feeding.",
        "buildLog": "Build completed successfully.\nC++20 / WASM\nAll source files compiled.",
        "byMap": {
            "Default": {"wins": 9, "draws": 0, "losses": 3},
            "Portals": {"wins": 8, "draws": 1, "losses": 2},
            "Devil": {"wins": 10, "draws": 0, "losses": 5},
        },
    },
    {
        "id": 103,
        "version": 3,
        "name": "nightjar-v3",
        "status": "idle",
        "language": "cpp",
        "wins": 37,
        "draws": 1,
        "losses": 28,
        "uploadedAt": "2026-10-01T08:15:00Z",
    },
    {
        "id": 102,
        "version": 2,
        "name": "nightjar-v2",
        "status": "idle",
        "language": "cpp",
        "wins": 29,
        "draws": 1,
        "losses": 17,
        "uploadedAt": "2026-09-30T12:30:00Z",
    },
    {
        "id": 101,
        "version": 1,
        "name": "nightjar-v1",
        "status": "failed",
        "language": "cpp",
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "uploadedAt": "2026-09-29T21:30:00Z",
        "buildLog": "main.cpp:12: error: expected ';'",
    },
]
BATTLES = [
    {
        "id": 720 + i,
        "at": f"2026-10-02T0{8 - i}:20:00Z",
        "opponent": name,
        "ranked": i % 3 != 0,
        "outcome": outcome,
        "results": results,
        "eloChange": change,
        "status": "completed",
    }
    for i, (name, outcome, results, change) in enumerate(
        [
            ("Paper crane", "win", ["win"], 0),
            ("Northstar", "win", ["win", "loss", "win", "win", "win"], 18),
            ("Atlas", "loss", ["loss", "win", "loss", "loss", "win"], -9),
            ("Compass", "win", ["win"], 0),
            ("Sundial", "draw", ["win", "loss", "draw", "win", "loss"], 2),
            ("Paper crane", "win", ["win", "win", "loss", "win", "win"], 14),
        ]
    )
]
LADDER = [
    {"id": i + 1, "name": name, "elo": 2130 - i * 37, "rank": i + 1}
    for i, name in enumerate(
        ["Northstar", "Atlas", "Paper crane", "Compass", "Sundial", "Low tide", "Orbit", "Monolith"]
    )
]
for index, team in enumerate(LADDER):
    team.update(
        wins=180 - index * 8,
        draws=3 + index,
        losses=20 + index * 9,
        members=[{"username": f"player{index + 1}"}],
        ranked=True,
    )
MAPS = [
    {"id": i + 1, "name": name}
    for i, name in enumerate(["Default", "Devil", "Portals", "Schooltime", "Trophy", "Autarky"])
]


class DemoAPI:
    demo = True
    credential = None

    async def close(self) -> None:
        pass

    async def get(self, path: str):
        if path == "/team":
            return deepcopy(TEAM)
        if path == "/me":
            return {"team": deepcopy(TEAM["team"]), "user": {"username": "demo"}}
        if path == "/submissions":
            return deepcopy(SUBMISSIONS)
        if path.startswith("/submissions/"):
            bot = next((b for b in SUBMISSIONS if b["id"] == int(path.split("/")[2])), {})
            return deepcopy(bot)
        if path.startswith("/battles?"):
            return deepcopy(BATTLES)
        if path.startswith("/battles/"):
            ident = int(path.split("/")[2])
            return {
                "match": {
                    "id": ident,
                    "status": "completed",
                    "ranked": True,
                    "teamAId": 900,
                    "teamBId": 1,
                },
                "teamAName": "Night shift",
                "teamBName": "Northstar",
                "games": [
                    {
                        "id": ident * 10 + i,
                        "mapName": name,
                        "status": "completed",
                        "winner": "a" if i != 1 else "b",
                        "hasReplay": True,
                    }
                    for i, name in enumerate(
                        ["Default", "Devil", "Portals", "Schooltime", "Trophy"]
                    )
                ],
            }
        if path == "/leaderboard":
            return deepcopy(LADDER)
        if path == "/maps":
            return deepcopy(MAPS)
        if path == "/queue":
            return {"queued": 3, "running": 8}
        raise APIError("Not available in the demo.", 404)

    async def activate(self, submission_id: int):
        raise APIError("Demo is read-only. Connect an account to activate a bot.")

    async def challenge(self, team_id: int, ranked: bool, map_ids: list[int]):
        raise APIError("Demo is read-only. Connect an account to request a game.")

    async def upload(self, *args):
        raise APIError("Demo is read-only. Connect an account to upload a bot.")

    async def download(self, path: str, destination: Path, **kwargs):
        raise APIError("Demo replays are placeholders. Connect an account to download real files.")
