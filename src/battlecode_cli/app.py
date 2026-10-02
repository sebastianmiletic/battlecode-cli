from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Input,
    OptionList,
    Select,
    SelectionList,
    Static,
)
from textual.widgets.option_list import Option

from .api import APIError, BattlecodeAPI
from .bots import prepare_bot
from .config import (
    SERVER,
    Credential,
    clear_credential,
    config_dir,
    download_dir,
    load_credential,
    redact,
    save_credential,
)
from .demo import DemoAPI
from .dialogs import Confirm, FilePicker, TextViewer
from .models import active_bot, battle_row, date_label, record_label, rows, team_data, winrate
from .views import Bots, Challenge, Games, Ladder, Overview, Settings, Upload

PAGES = [
    ("overview", "Overview"),
    ("bots", "Bots"),
    ("games", "Games"),
    ("ladder", "Ladder"),
    ("upload", "Upload"),
    ("challenge", "Challenge"),
    ("settings", "Settings"),
]
HELP = """BATTLECODE / CONTROL ROOM

1 Overview     2 Bots        3 Games       4 Ladder
5 Upload       6 Challenge   7 Settings

Tab / Shift+Tab  Move between controls
Arrows          Move through tables, menus and map selections
Enter           Inspect a row or use a focused control
Space           Toggle a practice map
r               Refresh (outside text fields)
Ctrl+p          Command palette
Ctrl+q          Quit
Escape          Close a dialog

Uploads, bot activation and challenges require confirmation.
Ranked challenges affect your rating. Practice is the default.
A new successful upload may become active automatically.

Win rate: wins / (wins + draws + losses), from server records.
Choose a battle, then a game, to download or open its replay.
VS Code needs the UNSW Battlecode replay viewer (unswbc vscode).

Demo data is synthetic. Demo mode cannot change an account.
Account settings, membership and tournaments use the website.
"""


class BattlecodeApp(App):
    TITLE = "Battlecode"
    SUB_TITLE = "Control room"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("1", "view('overview')", "Overview"),
        Binding("2", "view('bots')", "Bots"),
        Binding("3", "view('games')", "Games"),
        *[
            Binding(str(i), f"view('{page}')", label, show=False)
            for i, (page, label) in enumerate(PAGES[3:], 4)
        ],
        Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "help", "Help"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, *, demo: bool = False, refresh: int = 45, api=None):
        super().__init__()
        self.api = api or (DemoAPI() if demo else BattlecodeAPI(load_credential()))
        self.refresh_seconds = max(15, refresh)
        self.team: dict = {}
        self.submissions: list[dict] = []
        self.battles: list[dict] = []
        self.ladder: list[dict] = []
        self.maps: list[dict] = []
        self.bot_id: int | None = None
        self.battle_id: int | None = None
        self.game_id: int | None = None
        self.bot_details: dict = {}
        self.battle_details: dict = {}
        self.auth_failed = False
        self.syncing = False
        self.mutating = False
        self.cooldown_until = 0.0
        self.last_sync = "not synced"
        self.last_ladder_fetch = 0.0
        self.register_theme(
            Theme(
                name="battlecode-mono",
                primary="#e8e8e8",
                secondary="#a0a0a0",
                accent="#d0d0d0",
                foreground="#e8e8e8",
                background="#0c0c0c",
                surface="#161616",
                panel="#202020",
                boost="#aaaaaa",
                success="#d0d0d0",
                error="#e8e8e8",
                warning="#b8b8b8",
                dark=True,
            )
        )
        self.theme = "battlecode-mono"

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("BATTLECODE  /  CONTROL ROOM", id="wordmark", markup=False)
            yield Static(
                "DEMO / OFFLINE" if self.api.demo else "CONNECTING", id="connection", markup=False
            )
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Static("WORKSPACE", id="sidebar-label")
                yield OptionList(
                    *[
                        Option(Text(f"{i} {label}"), id=page)
                        for i, (page, label) in enumerate(PAGES, 1)
                    ],
                    id="nav",
                )
                yield Static("API v1\nKeyboard first\n? for help", id="sidebar-note", markup=False)
            with ContentSwitcher(initial="overview", id="pages"):
                yield Overview(id="overview", classes="page")
                yield Bots(id="bots", classes="page")
                yield Games(id="games", classes="page")
                yield Ladder(id="ladder", classes="page")
                yield Upload(id="upload", classes="page")
                yield Challenge(id="challenge", classes="page")
                yield Settings(id="settings", classes="page")
        yield Static(
            "Demo: synthetic data, no API calls." if self.api.demo else "Connecting…",
            id="status-line",
            markup=False,
        )
        yield Footer()

    def on_mount(self) -> None:
        columns = {
            "overview-games": ("OPPONENT", "RESULT", "MODE", "Δ ELO"),
            "overview-bots": ("BOT", "STATE", "WIN RATE"),
            "bots-table": ("VERSION", "BOT", "STATE", "W / D / L", "WIN RATE", "UPLOADED"),
            "games-table": ("BATTLE", "OPPONENT", "MODE", "RESULT", "GAMES", "Δ ELO", "WHEN"),
            "game-parts": ("GAME", "MAP", "STATUS", "RESULT", "REPLAY"),
            "ladder-table": ("RANK", "TEAM", "ELO", "TEAM ID"),
        }
        for ident, headings in columns.items():
            table = self.query_one(f"#{ident}", DataTable)
            table.add_columns(*headings)
            table.show_row_labels = False
        self.query_one("#nav", OptionList).highlighted = 0
        self.query_one("#overview-games", DataTable).focus()
        self.update_settings()
        self.set_class(self.size.width < 110, "compact")
        self.set_class(self.size.height < 32, "short")
        self.set_interval(self.refresh_seconds, self.poll)
        self.refresh_data()

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 110, "compact")
        self.set_class(event.size.height < 32, "short")

    async def on_unmount(self) -> None:
        await self.api.close()

    def status(self, message: str) -> None:
        token = self.api.credential.token if self.api.credential else ""
        self.query_one("#status-line", Static).update(redact(message, token))

    def fail(self, error: Exception, field: str | None = None) -> None:
        token = self.api.credential.token if self.api.credential else ""
        message = redact(str(error), token)
        self.status(message)
        if field:
            self.query_one(field, Static).update(message)
        self.notify(message, title="Could not complete action", severity="error", timeout=8)

    def update_settings(self) -> None:
        credential = self.api.credential
        state = "Demo mode. Synthetic data; account-changing actions are disabled."
        if not self.api.demo:
            state = (
                f"Key source: {credential.source}\nServer: {SERVER}"
                if credential
                else "No key found. Connect below or run battlecode auth set."
            )
            if self.auth_failed:
                state += "\nThe saved key was rejected. Create a new key on your team page."
        self.query_one("#connection-summary", Static).update(state)
        self.query_one("#local-paths", Static).update(
            f"Config: {config_dir()}\nDownloads: {download_dir()}\nAuto-refresh: {self.refresh_seconds}s"
        )
        for ident in ("connect-key", "forget-key"):
            self.query_one(f"#{ident}", Button).disabled = self.api.demo

    def action_view(self, page: str) -> None:
        if page not in dict(PAGES) or len(self.screen_stack) > 1:
            return
        self.query_one("#pages", ContentSwitcher).current = page
        self.query_one("#nav", OptionList).highlighted = [p for p, _ in PAGES].index(page)
        focus = {
            "overview": "overview-games",
            "bots": "bots-table",
            "games": "games-table",
            "ladder": "ladder-search",
            "upload": "upload-path",
            "challenge": "challenge-team",
            "settings": "api-key",
        }
        self.query_one(f"#{focus[page]}").focus()
        if page == "ladder" and not self.ladder:
            self.refresh_data()
        if page == "challenge" and not self.maps:
            self.refresh_data()

    @on(OptionList.OptionSelected, "#nav")
    def nav_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_view(str(event.option.id))

    def action_help(self) -> None:
        self.push_screen(TextViewer("Keyboard guide", HELP))

    def poll(self) -> None:
        if (
            not self.auth_failed
            and not self.syncing
            and not self.mutating
            and time.monotonic() >= self.cooldown_until
        ):
            self.refresh_data()

    def action_refresh(self) -> None:
        if self.syncing:
            return
        if time.monotonic() < self.cooldown_until:
            self.status("Rate limited. Waiting for the server's retry window.")
            return
        self.auth_failed = False
        self.refresh_data()

    @work(group="sync", exclusive=True)
    async def refresh_data(self) -> None:
        self.syncing = True
        self.status("Refreshing…" if self.team else "Connecting to Battlecode…")
        endpoints = ["/team", "/submissions", "/battles?limit=50"]
        if not self.ladder or time.monotonic() - self.last_ladder_fetch > 120:
            endpoints.append("/leaderboard")
        if not self.maps:
            endpoints.append("/maps")
        try:
            results = await asyncio.gather(
                *(self.api.get(p) for p in endpoints), return_exceptions=True
            )
            errors: list[Exception] = []
            for path, result in zip(endpoints, results, strict=True):
                if isinstance(result, Exception):
                    errors.append(result)
                    if isinstance(result, APIError):
                        if result.status == 401:
                            self.auth_failed = True
                        if result.status == 429:
                            self.cooldown_until = time.monotonic() + result.retry_after
                    continue
                if path == "/team":
                    self.team = result if isinstance(result, dict) else {}
                elif path == "/submissions":
                    self.submissions = sorted(
                        rows(result, "submissions"), key=lambda s: s.get("id", 0), reverse=True
                    )
                elif path.startswith("/battles"):
                    self.battles = rows(result, "battles", "matches")
                elif path == "/leaderboard":
                    self.ladder = rows(result, "teams", "leaderboard", "ratings")
                    self.last_ladder_fetch = time.monotonic()
                elif path == "/maps":
                    self.maps = rows(result, "maps")
                    selection = self.query_one("#challenge-maps", SelectionList)
                    previous = set(selection.selected)
                    selection.clear_options()
                    selection.add_options(
                        [
                            (
                                Text(str(m.get("name", m.get("title", "Map")))),
                                int(m["id"]),
                                int(m["id"]) in previous,
                            )
                            for m in self.maps
                            if "id" in m
                        ]
                    )
            self.render_data()
            if errors:
                self.query_one("#connection", Static).update(
                    "KEY REJECTED" if self.auth_failed else "STALE"
                )
                self.status(f"{errors[0]} Last successful sync: {self.last_sync}.")
                if self.auth_failed and not self.team:
                    self.action_view("settings")
            else:
                self.last_sync = datetime.now().strftime("%H:%M:%S")
                self.query_one("#connection", Static).update(
                    "DEMO / OFFLINE" if self.api.demo else "CONNECTED"
                )
                self.status(
                    f"{'DEMO · synthetic data · ' if self.api.demo else ''}Updated {self.last_sync}  ·  refresh {self.refresh_seconds}s  ·  ? help"
                )
            self.update_settings()
            if (
                self.battle_id
                and self.battle_details
                and self.query_one("#pages", ContentSwitcher).current == "games"
                and self.battle_details.get("match", {}).get("status")
                in ("pending", "queued", "running", "processing")
            ):
                self.inspect_battle(focus=False)
        except Exception as error:
            self.fail(error)
        finally:
            self.syncing = False

    @staticmethod
    def fill(table: DataTable, data: list[tuple[str, tuple]]) -> None:
        old_row = table.cursor_row
        table.clear()
        for key, cells in data:
            table.add_row(*[Text(str(value)) for value in cells], key=key)
        if data:
            table.move_cursor(row=min(old_row, len(data) - 1))

    def render_data(self) -> None:
        team = team_data(self.team)
        active = active_bot(self.submissions)
        name = str(team.get("name", "Your team"))
        summary = Text(name + "\n", style="bold")
        summary.append(
            f"\nRating  {team.get('elo', team.get('rating', 'n/a'))}     Rank  #{self.team.get('rank', team.get('rank', 'n/a'))}",
            style="bold",
        )
        summary.append(
            f"\n{record_label(team)}   ·   {winrate(team)} wins   ·   Peak {self.team.get('peak', 'n/a')}",
            style="#a0a0a0",
        )
        self.query_one("#team-summary", Static).update(summary)
        self.query_one("#active-summary", Static).update(
            f"ACTIVE  {active['name']}   ·   {winrate(active)} wins   ·   {record_label(active)}"
            if active
            else "No active bot. Upload a new version or activate a built submission."
        )
        self.query_one("#overview-empty").display = not bool(self.team)
        self.fill(
            self.query_one("#overview-bots", DataTable),
            [
                (
                    str(s["id"]),
                    (
                        s.get("name", "Untitled"),
                        str(s.get("status", "unknown")).upper(),
                        winrate(s),
                    ),
                )
                for s in self.submissions[:8]
            ],
        )
        self.fill(
            self.query_one("#bots-table", DataTable),
            [
                (
                    str(s["id"]),
                    (
                        f"v{s.get('version', '?')}",
                        s.get("name", "Untitled"),
                        str(s.get("status", "unknown")).upper(),
                        record_label(s),
                        winrate(s),
                        date_label(s.get("uploadedAt")),
                    ),
                )
                for s in self.submissions
            ],
        )
        self.query_one("#bots-empty").display = not bool(self.submissions)
        our_id = team.get("id")
        normalized = [battle_row(b, our_id) for b in self.battles]
        self.fill(
            self.query_one("#overview-games", DataTable),
            [
                (str(b["id"]), (b["opponent"], b["result"], b["mode"], b["change"]))
                for b in normalized[:12]
                if b["id"]
            ],
        )
        self.fill(
            self.query_one("#games-table", DataTable),
            [
                (
                    str(b["id"]),
                    (
                        b["id"],
                        b["opponent"],
                        b["mode"],
                        b["result"],
                        b["score"],
                        b["change"],
                        b["at"],
                    ),
                )
                for b in normalized
                if b["id"]
            ],
        )
        self.query_one("#games-empty").display = not bool(normalized)
        self.render_ladder()
        if self.bot_id:
            bot = next((s for s in self.submissions if s["id"] == self.bot_id), None)
            if bot:
                self.display_bot(bot)

    def render_ladder(self) -> None:
        search = self.query_one("#ladder-search", Input).value.casefold()
        selected = [
            (i, t)
            for i, t in enumerate(self.ladder, 1)
            if search in str(t.get("name", "")).casefold()
            or search in str(t.get("id", t.get("teamId", "")))
        ]
        self.fill(
            self.query_one("#ladder-table", DataTable),
            [
                (
                    str(t.get("id", t.get("teamId"))),
                    (
                        t.get("rank", i),
                        t.get("name", "Team"),
                        t.get("elo", t.get("rating", "n/a")),
                        t.get("id", t.get("teamId")),
                    ),
                )
                for i, t in selected
            ],
        )
        empty = self.query_one("#ladder-empty", Static)
        empty.display = not bool(selected)
        empty.update(
            "No teams match your search."
            if self.ladder
            else "Connect your account to load the ladder."
        )
        self.query_one("#ladder-challenge", Button).disabled = not bool(selected)

    @on(Input.Changed, "#ladder-search")
    def ladder_search(self) -> None:
        self.render_ladder()

    @on(DataTable.RowHighlighted, "#bots-table")
    def bot_highlight(self, event: DataTable.RowHighlighted) -> None:
        self.bot_id = int(str(event.row_key.value))
        bot = next((s for s in self.submissions if s["id"] == self.bot_id), {})
        self.display_bot(bot)

    def display_bot(self, bot: dict) -> None:
        if not bot:
            return
        self.query_one("#bot-detail", Static).update(
            f"{bot.get('name', 'Bot')}  /  #{bot.get('id')}  /  {bot.get('language', '?')}  /  {str(bot.get('status', '')).upper()}\n"
            f"{record_label(bot)}   ·   {winrate(bot)} win rate   ·   {bot.get('description') or 'Enter to load build and per-map details.'}"
        )
        status = str(bot.get("status", ""))
        self.query_one("#activate-bot", Button).disabled = (
            self.api.demo
            or status not in ("idle", "inactive", "ready", "built", "success")
            or self.mutating
        )
        for ident in ("bot-log", "download-bot"):
            self.query_one(f"#{ident}", Button).disabled = False

    @on(DataTable.RowSelected, "#bots-table")
    def bot_selected(self, event: DataTable.RowSelected) -> None:
        self.bot_id = int(str(event.row_key.value))
        self.inspect_bot()

    @on(DataTable.RowSelected, "#overview-bots")
    def overview_bot(self, event: DataTable.RowSelected) -> None:
        self.action_view("bots")
        self.bot_id = int(str(event.row_key.value))
        table = self.query_one("#bots-table", DataTable)
        index = next((i for i, s in enumerate(self.submissions) if s["id"] == self.bot_id), 0)
        table.move_cursor(row=index)
        self.inspect_bot()

    @work(group="bot-detail", exclusive=True)
    async def inspect_bot(self) -> None:
        ident = self.bot_id
        if ident is None:
            return
        try:
            raw = await self.api.get(f"/submissions/{ident}")
            detail = raw.get("submission", raw)
            if self.bot_id != ident:
                return
            self.bot_details = detail
            self.display_bot(detail)
            parts = [
                f"{detail.get('name', 'Bot')} / #{ident}",
                f"{record_label(detail)}   ·   {winrate(detail)} win rate",
                "",
                "PER-MAP RECORDS",
            ]
            by_map = detail.get("byMap") or {}
            if isinstance(by_map, dict):
                parts.extend(
                    f"{name:24}  {record_label(rec):20} {winrate(rec)}"
                    for name, rec in by_map.items()
                    if isinstance(rec, dict)
                )
            if not by_map:
                parts.append("No per-map records yet.")
            parts += ["", "BUILD LOG", redact(detail.get("buildLog") or "No build log supplied.")]
            self.push_screen(TextViewer(f"Submission #{ident}", "\n".join(parts)))
        except Exception as error:
            self.fail(error)

    @on(DataTable.RowSelected, "#games-table")
    @on(DataTable.RowSelected, "#overview-games")
    def battle_selected(self, event: DataTable.RowSelected) -> None:
        self.battle_id = int(str(event.row_key.value))
        self.action_view("games")
        normalized = [battle_row(b, team_data(self.team).get("id")) for b in self.battles]
        index = next((i for i, b in enumerate(normalized) if b["id"] == self.battle_id), 0)
        self.query_one("#games-table", DataTable).move_cursor(row=index)
        self.inspect_battle()

    @work(group="game-detail", exclusive=True)
    async def inspect_battle(self, focus: bool = True) -> None:
        ident = self.battle_id
        if ident is None:
            return
        self.game_id = None
        self.battle_details = {}
        self.fill(self.query_one("#game-parts", DataTable), [])
        self.query_one("#game-detail", Static).update(f"Loading battle #{ident}…")
        for name in ("download-replay", "open-replay", "view-game", "game-log"):
            self.query_one(f"#{name}", Button).disabled = True
        try:
            detail = await self.api.get(f"/battles/{ident}")
            if self.battle_id != ident:
                return
            self.battle_details = detail
            match = detail.get("match", detail)
            games = rows(detail, "games")
            if not games and match.get("mapId"):
                games = [
                    dict(
                        match,
                        mapName=detail.get("mapName"),
                        hasReplay=match.get("status") == "completed",
                    )
                ]
                self.battle_details["games"] = games
            side = "B" if match.get("teamBId") == team_data(self.team).get("id") else "A"
            cells = []
            for game in games:
                winner = str(game.get("winner") or "").upper()
                result = (
                    "WIN"
                    if winner == side
                    else "LOSS"
                    if winner in ("A", "B")
                    else "DRAW"
                    if game.get("status") == "completed"
                    else "·"
                )
                cells.append(
                    (
                        str(game["id"]),
                        (
                            game["id"],
                            game.get("mapName", "Map"),
                            game.get("status", "pending"),
                            result,
                            "Ready" if game.get("hasReplay") else "Not ready",
                        ),
                    )
                )
            self.fill(self.query_one("#game-parts", DataTable), cells)
            note = f"Battle #{ident}  ·  {match.get('status', 'unknown')}  ·  {len(games)} games"
            if detail.get("queuePosition") is not None:
                note += f"  ·  queue #{detail['queuePosition']}"
            self.query_one("#game-detail", Static).update(note)
            self.query_one("#view-game", Button).disabled = False
            self.query_one("#game-log", Button).disabled = not bool(detail.get("log"))
            if cells and focus:
                self.query_one("#game-parts", DataTable).focus()
        except Exception as error:
            self.fail(error)
            self.query_one("#game-detail", Static).update(
                f"Could not load battle #{ident}. Press Enter to retry."
            )

    @on(DataTable.RowHighlighted, "#game-parts")
    def game_highlight(self, event: DataTable.RowHighlighted) -> None:
        self.game_id = int(str(event.row_key.value))
        game = next(
            (g for g in rows(self.battle_details, "games") if g.get("id") == self.game_id), {}
        )
        ready = bool(game.get("hasReplay"))
        for name in ("download-replay", "open-replay"):
            self.query_one(f"#{name}", Button).disabled = not ready

    @on(DataTable.RowSelected, "#ladder-table")
    def ladder_selected(self, event: DataTable.RowSelected) -> None:
        self.prepare_challenge(int(str(event.row_key.value)))

    def prepare_challenge(self, ident: int) -> None:
        self.action_view("challenge")
        self.query_one("#challenge-team", Input).value = str(ident)
        team = next((t for t in self.ladder if t.get("id", t.get("teamId")) == ident), {})
        self.query_one("#challenge-status", Static).update(
            f"Opponent: {team.get('name', f'team #{ident}')}\nPlaying with: {(active_bot(self.submissions) or {}).get('name', 'no active bot')}"
        )

    @on(Select.Changed, "#challenge-mode")
    def mode_changed(self, event: Select.Changed) -> None:
        ranked = event.value == "ranked"
        self.query_one("#challenge-maps", SelectionList).disabled = ranked
        self.query_one("#challenge-note", Static).update(
            "Ranked: five games, random maps, affects your rating and challenge quota."
            if ranked
            else "Practice: no rating change. Choose maps or let the server choose."
        )

    def begin_mutation(self) -> bool:
        if self.api.demo:
            self.notify(
                "Demo is read-only. Connect your account to use this action.", title="Demo mode"
            )
            return False
        if self.mutating:
            self.notify("Another action is still running. Wait for it to finish.")
            return False
        if self.auth_failed or not self.api.credential:
            self.action_view("settings")
            self.notify("Connect a valid API key before changing your account.")
            return False
        self.mutating = True
        return True

    @work(group="mutation")
    async def activate_selected(self) -> None:
        bot = next((s for s in self.submissions if s["id"] == self.bot_id), None)
        if not bot or not self.begin_mutation():
            return
        try:
            current = active_bot(self.submissions)
            accepted = await self.push_screen_wait(
                Confirm(
                    "Change active bot?",
                    f"Activate {bot.get('name')} (#{bot['id']})?\n\nThis replaces {(current or {}).get('name', 'your current bot')} for future battles. Games already queued may still use the previous bot.",
                    "Activate bot",
                )
            )
            if accepted:
                self.status("Activating bot…")
                await self.api.activate(bot["id"])
                self.notify(f"{bot.get('name')} is now active.", title="Bot activated")
                self.refresh_data()
        except Exception as error:
            self.fail(error)
        finally:
            self.mutating = False

    @work(group="mutation")
    async def review_upload(self) -> None:
        path = self.query_one("#upload-path", Input).value
        name = self.query_one("#upload-name", Input).value.strip()
        description = self.query_one("#upload-description", Input).value.strip()
        if not path.strip() or not name:
            self.query_one("#upload-status", Static).update(
                "Choose a bot path and give this version a name."
            )
            return
        if not self.begin_mutation():
            return
        button = self.query_one("#review-upload", Button)
        button.disabled = True
        try:
            self.query_one("#upload-status", Static).update(
                "Checking source files and preparing archive…"
            )
            bot = await asyncio.to_thread(prepare_bot, path)
            accepted = await self.push_screen_wait(
                Confirm(
                    "Upload this version?",
                    f"{name}\n{bot.path}\n\n{bot.language} · {len(bot.files)} files · {len(bot.blob) / 1024:.1f} KB\nSHA-256: {bot.digest[:24]}…\n\nA successful server build may automatically become active. No local source code is executed.",
                    "Upload bot",
                )
            )
            if not accepted:
                self.query_one("#upload-status", Static).update(
                    "Upload cancelled. Nothing was sent."
                )
                return
            self.query_one("#upload-status", Static).update("Uploading once. Please wait…")
            reply = await self.api.upload(name, description, bot.language, bot.blob)
            self.query_one("#upload-status", Static).update(
                f"Uploaded {name}. Version {reply.get('version', '?')} · {reply.get('status', 'processing')}. Watch its build on the Bots page."
            )
            self.notify("Upload accepted. The dashboard will poll the build status.", title=name)
            self.refresh_data()
        except Exception as error:
            self.fail(error, "#upload-status")
        finally:
            self.mutating = False
            button.disabled = False

    @work(group="mutation")
    async def review_challenge(self) -> None:
        value = self.query_one("#challenge-team", Input).value.strip()
        if not value.isdigit() or int(value) <= 0:
            self.query_one("#challenge-status", Static).update(
                "Enter a positive opponent team ID, or choose a team on the Ladder page."
            )
            return
        ident = int(value)
        if ident == team_data(self.team).get("id"):
            self.query_one("#challenge-status", Static).update(
                "Choose another team, not your own team."
            )
            return
        active = active_bot(self.submissions)
        if not active:
            self.query_one("#challenge-status", Static).update(
                "You need an active bot before requesting a battle."
            )
            return
        if not self.begin_mutation():
            return
        button = self.query_one("#review-challenge", Button)
        button.disabled = True
        try:
            ranked = self.query_one("#challenge-mode", Select).value == "ranked"
            maps = (
                list(self.query_one("#challenge-maps", SelectionList).selected)
                if not ranked
                else []
            )
            opponent = next(
                (t.get("name") for t in self.ladder if t.get("id", t.get("teamId")) == ident),
                f"Team #{ident}",
            )
            note = (
                "Five games on server-selected maps. Affects your rating."
                if ranked
                else f"Practice only. No rating change. {len(maps) if maps else 'Server-selected'} map(s)."
            )
            accepted = await self.push_screen_wait(
                Confirm(
                    "Request a ranked battle?" if ranked else "Request a practice game?",
                    f"{opponent} (#{ident})\nYour bot: {active.get('name')}\n\n{note}\nCounts towards your server challenge quota.",
                    "Request battle",
                )
            )
            if not accepted:
                self.query_one("#challenge-status", Static).update(
                    "Challenge cancelled. No game requested."
                )
                return
            reply = await self.api.challenge(ident, ranked, maps)
            identifiers = reply.get("ids") or [reply.get("id", "queued")]
            self.query_one("#challenge-status", Static).update(
                f"Battle requested: {', '.join(map(str, identifiers))}. Open Games to watch the queue and results."
            )
            self.notify("The server accepted your challenge.", title="Battle requested")
            self.refresh_data()
        except Exception as error:
            self.fail(error, "#challenge-status")
        finally:
            self.mutating = False
            button.disabled = False

    @work(group="auth", exclusive=True)
    async def connect_key(self) -> None:
        if self.mutating:
            self.notify("Wait for the current action to finish before changing accounts.")
            return
        field = self.query_one("#api-key", Input)
        token = field.value.strip()
        field.value = ""
        if not token.startswith("bc_"):
            self.query_one("#settings-status", Static).update(
                "Use an API key from your team page, starting with bc_."
            )
            return
        override = next(
            (k for k in ("BATTLECODE_API_KEY", "UNSWBC_KEY") if os.environ.get(k)), None
        )
        if override:
            self.query_one("#settings-status", Static).update(
                f"{override} overrides saved keys. Unset it in your shell, then restart to connect a different account."
            )
            return
        candidate = BattlecodeAPI(Credential(token, str(config_dir() / "credentials.json")))
        button = self.query_one("#connect-key", Button)
        button.disabled = True
        try:
            self.query_one("#settings-status", Static).update("Verifying your key…")
            who = await candidate.get("/me")
            save_credential(token)
            for group in ("sync", "bot-detail", "game-detail", "download"):
                self.workers.cancel_group(self, group)
            await asyncio.sleep(0)
            old = self.api
            self.api = candidate
            await old.close()
            self.reset_account()
            self.query_one("#settings-status", Static).update(
                f"Connected to {(who.get('team') or {}).get('name', 'your team')}. Key saved with owner-only permissions."
            )
            self.update_settings()
            self.refresh_data()
        except Exception as error:
            await candidate.close()
            self.fail(APIError(redact(str(error), token)), "#settings-status")
        finally:
            button.disabled = False

    def reset_account(self) -> None:
        self.team = {}
        self.submissions = []
        self.battles = []
        self.ladder = []
        self.maps = []
        self.bot_id = self.battle_id = self.game_id = None
        self.bot_details = {}
        self.battle_details = {}
        self.auth_failed = False
        self.cooldown_until = 0
        self.fill(self.query_one("#game-parts", DataTable), [])
        self.query_one("#bot-detail", Static).update("Choose a submission above.")
        self.query_one("#game-detail", Static).update("Choose a battle above.")
        for ident in (
            "activate-bot",
            "bot-log",
            "download-bot",
            "download-replay",
            "open-replay",
            "view-game",
            "game-log",
        ):
            self.query_one(f"#{ident}", Button).disabled = True
        self.render_data()

    @work(group="mutation")
    async def forget_key(self) -> None:
        if self.api.demo or self.mutating:
            return
        self.mutating = True
        try:
            accepted = await self.push_screen_wait(
                Confirm(
                    "Forget this app's key?",
                    "Only this app's saved key is removed. Toolkit credentials and environment variables are not changed. The app reconnects using the next available key.",
                    "Forget key",
                )
            )
            if accepted:
                clear_credential()
                for group in ("sync", "bot-detail", "game-detail", "download"):
                    self.workers.cancel_group(self, group)
                await asyncio.sleep(0)
                old = self.api
                self.api = BattlecodeAPI(load_credential())
                await old.close()
                self.reset_account()
                self.update_settings()
                self.query_one("#settings-status", Static).update(
                    "App key removed. Reconnecting with the next available credential source."
                )
                self.refresh_data()
        except Exception as error:
            self.fail(error, "#settings-status")
        finally:
            self.mutating = False

    @work(group="download", exclusive=True)
    async def download_bot(self) -> None:
        if self.bot_id is None:
            return
        ident = self.bot_id
        try:
            self.status(f"Downloading submission #{ident}…")
            path = await self.api.download(
                f"/submissions/{ident}/download",
                download_dir() / f"submission-{ident}.zip",
                max_bytes=4 * 1024 * 1024,
            )
            self.status(f"Saved {path}")
            self.notify(str(path), title="Bot ZIP saved")
        except Exception as error:
            self.fail(error)

    @work(group="download", exclusive=True)
    async def download_replay(self, open_after: bool = False) -> None:
        if self.game_id is None:
            return
        ident = self.game_id
        try:
            self.status(f"Downloading replay #{ident}…")
            path = await self.api.download(
                f"/battles/{ident}/replay", download_dir() / f"{ident}.replay"
            )
            self.status(f"Saved {path}")
            if open_after:
                await asyncio.to_thread(self.open_local_replay, path)
                self.notify(
                    "Opened locally. VS Code needs the UNSW Battlecode replay viewer.",
                    title="Replay saved",
                )
            else:
                self.notify(str(path), title="Replay saved")
        except Exception as error:
            self.fail(error)

    @staticmethod
    def open_local_replay(path: Path) -> None:
        code = shutil.which("code")
        env = {k: v for k, v in os.environ.items() if k not in ("BATTLECODE_API_KEY", "UNSWBC_KEY")}
        if code:
            result = subprocess.run(
                [code, "--reuse-window", str(path)], capture_output=True, timeout=15, env=env
            )
        elif sys.platform == "darwin":
            result = subprocess.run(["open", str(path)], capture_output=True, timeout=15, env=env)
        elif sys.platform == "win32":
            os.startfile(str(path))
            return
        elif shutil.which("xdg-open"):
            result = subprocess.run(
                ["xdg-open", str(path)], capture_output=True, timeout=15, env=env
            )
        else:
            raise APIError(f"Replay saved to {path}. No file opener was found.")
        if result.returncode:
            raise APIError(f"Replay saved to {path}, but your file viewer could not open it.")

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id
        if ident in ("home-upload", "bots-upload"):
            self.action_view("upload")
        elif ident == "home-challenge":
            self.action_view("challenge")
        elif ident == "activate-bot":
            self.activate_selected()
        elif ident == "bot-log":
            self.inspect_bot()
        elif ident == "download-bot":
            self.download_bot()
        elif ident in ("download-replay", "open-replay"):
            self.download_replay(open_after=ident == "open-replay")
        elif ident == "view-game" and self.battle_id:
            webbrowser.open(f"{SERVER}/battles/{self.battle_id}")
        elif ident == "game-log":
            self.push_screen(
                TextViewer(
                    "Judge log", redact(str(self.battle_details.get("log") or "No log supplied."))
                )
            )
        elif ident == "ladder-challenge":
            table = self.query_one("#ladder-table", DataTable)
            if table.row_count:
                key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
                self.prepare_challenge(int(str(key.value)))
        elif ident == "browse-bot":
            self.push_screen(
                FilePicker(self.query_one("#upload-path", Input).value), self.picked_file
            )
        elif ident == "review-upload":
            self.review_upload()
        elif ident == "review-challenge":
            self.review_challenge()
        elif ident == "connect-key":
            self.connect_key()
        elif ident == "forget-key":
            self.forget_key()
        elif ident == "team-page":
            webbrowser.open(f"{SERVER}/team")

    def picked_file(self, path: str | None) -> None:
        if path:
            self.query_one("#upload-path", Input).value = path
            name = self.query_one("#upload-name", Input)
            if not name.value:
                name.value = Path(path.strip().strip("\"'")).stem
