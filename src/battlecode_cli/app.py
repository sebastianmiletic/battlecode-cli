from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from importlib.resources import files
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
    Digits,
    Footer,
    Input,
    OptionList,
    Select,
    SelectionList,
    Static,
    TabbedContent,
)
from textual.widgets.option_list import Option

from .api import APIError, BattlecodeAPI
from .arena_ui import ArenaActions
from .bots import prepare_bot
from .charts import HistoryChart
from .config import (
    KEY_PATTERN,
    SERVER,
    AccountStore,
    Credential,
    config_dir,
    download_dir,
    importable_credential,
    redact,
)
from .demo import DemoAPI
from .dialogs import Confirm, FilePicker, TextViewer
from .history import HistoryStore, histories, number, series
from .models import (
    active_bot,
    battle_row,
    date_label,
    leaderboard_rows,
    members_label,
    record_label,
    rows,
    team_data,
    winrate,
)
from .replay_viewer import ReplayViewer
from .replays import ReplayLibrary, decode_replay, load_replay
from .views import Arena, Bots, Games, Leaderboard, Overview, Settings

PAGES = [
    ("overview", "Overview"),
    ("bots", "Bots"),
    ("games", "Games"),
    ("arena", "Arena"),
    ("leaderboard", "Leaderboard"),
    ("settings", "API keys"),
]
HELP = """BATTLECODE

1 Overview     2 Bots        3 Games
4 Arena        5 Leaderboard             6 API keys

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
Games includes ranked/unranked matches and the local replay library.
Bots includes versions and uploads. Arena includes benchmarks, online challenges and results.
Local benchmarks use the official judge sandbox with fixed seeds and optional seat swaps.
Import up to 1,000 custom maps from a folder or ZIP. They are never uploaded.
Compare two local versions, optionally against up to 20 shared opponent bots.
Results include per-map records and every recorded move, growth, split and death.
Private opponent source is not available through the API. Use Online to challenge other teams.
ELO history comes from server history. Inferred rank uses today's eligible teams.
Observed ELO and rank snapshots are also retained locally.
The built-in viewer supports play, pause, step, mouse seeking and tile inspection.
Local files are not uploaded to the server. No account is needed to view them.

API keys: add, save, switch, disconnect or delete. Exactly one account is active.
First launch asks for a key; Continue offline opens your local replay library.
Mouse: click navigation, table rows and buttons; use the wheel to scroll.

Demo data is synthetic. Demo mode cannot change an account.
Account settings, membership and tournaments use the website.
"""


class BattlecodeApp(ArenaActions, App):
    TITLE = "Battlecode"
    SUB_TITLE = ""
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

    def __init__(
        self,
        *,
        demo: bool = False,
        refresh: int = 45,
        api=None,
        accounts=None,
        library=None,
        replay_path: str | None = None,
    ):
        super().__init__()
        self.accounts = accounts or AccountStore()
        self.library = library or ReplayLibrary()
        self.account_id: str | None = None
        self.saved_profile_count = 0
        self.local_replay_id: str | None = None
        self.local_replays: list[dict] = []
        self.replay_path = replay_path
        self.custom_api = api is not None
        self.store_error = ""
        self.auth_changing = False
        self.importable = None if demo else importable_credential()
        credential = None
        if not demo and api is None:
            try:
                credential = self.accounts.active_credential()
            except (ValueError, OSError) as error:
                self.store_error = str(error)
        self.api = api or (DemoAPI() if demo else BattlecodeAPI(credential))
        self.refresh_seconds = max(15, refresh)
        self.team: dict = {}
        self.submissions: list[dict] = []
        self.battles: list[dict] = []
        self.ladder: list[dict] = []
        self.maps: list[dict] = []
        self.history_store = HistoryStore()
        self.init_arena()
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
            yield Static("BATTLECODE", id="wordmark", markup=False)
            yield Button("See more of my projects", id="projects-link")
        yield Static("", id="connection", classes="hidden", markup=False)
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Static("", id="sidebar-label")
                yield OptionList(
                    *[
                        Option(Text(f"{i} {label}"), id=page)
                        for i, (page, label) in enumerate(PAGES, 1)
                    ],
                    id="nav",
                )
                yield Static("? Help", id="sidebar-note", markup=False)
            with ContentSwitcher(initial="overview", id="pages"):
                yield Overview(id="overview", classes="page")
                yield Bots(id="bots", classes="page")
                yield Games(id="games", classes="page")
                yield Arena(id="arena", classes="page")
                yield Leaderboard(id="leaderboard", classes="page")
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
            "ladder-table": ("RANK", "TEAM", "ELO", "WIN RATE", "WINS", "MEMBERS", "TEAM ID"),
            "arena-runs-table": ("WHEN", "STATUS", "GAMES", "BOT A", "BOT B"),
            "arena-results-table": (
                "GAME",
                "MAP",
                "BOT A",
                "BOT B",
                "WINNER",
                "ROUNDS",
                "MOVES A/B",
                "DEATHS A/B",
            ),
            "accounts-table": ("LABEL", "TEAM", "STATE", "KEY STORAGE"),
            "replays-table": ("FILE", "MAP", "ROUNDS", "WINNER", "IMPORTED"),
        }
        widths = {
            "overview-games": (20, 8, 8, 7),
            "games-table": (6, 20, 8, 9, 12, 7, 12),
            "ladder-table": (4, 24, 6, 8, 7, 30, 7),
        }
        for ident, headings in columns.items():
            table = self.query_one(f"#{ident}", DataTable)
            for index, heading in enumerate(headings):
                table.add_column(heading, width=widths[ident][index] if ident in widths else None)
            table.show_row_labels = False
        self.query_one("#nav", OptionList).highlighted = 0
        self.query_one("#overview-games", DataTable).focus()
        self.update_settings()
        self.set_class(self.size.width < 110, "compact")
        self.set_class(self.size.height < 32, "short")
        self.set_interval(self.refresh_seconds, self.poll)
        self.render_library()
        self.render_arena_maps()
        self.render_arena_runs()
        self.update_arena_runner()
        if self.api.demo or self.api.credential:
            self.refresh_data()
        else:
            self.render_data()
            self.action_view("settings")
            self.query_one("#connection", Static).update("OFFLINE / SETUP")
            self.status(
                self.store_error
                or "Welcome. Add your API key, or continue offline to view replays."
            )
        if self.replay_path:
            self.open_replay_file(self.replay_path, import_file=False)

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 110, "compact")
        self.set_class(event.size.height < 32, "short")

    async def on_unmount(self) -> None:
        if self.arena_runner:
            self.arena_runner.cancel.set()
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
        if self._closing or self.query_one_optional("#settings-title", Static) is None:
            return
        credential = self.api.credential
        profiles = []
        active_id = None
        state = "Demo: synthetic account data. API-key changes are disabled."
        if not self.api.demo:
            try:
                profiles = self.accounts.accounts()
                active_id = self.accounts.active_id
            except (ValueError, OSError) as error:
                self.store_error = str(error)
            state = (
                f"Connected: {credential.source}\nOnly this account is active."
                if credential
                else "Connect your Battlecode account. Paste a key below, or continue offline to view local replays."
            )
            if self.auth_failed:
                state += "\nThis key was rejected. Add a new key from your team page."
            if self.store_error:
                state += f"\n{self.store_error}"
        self.query_one("#settings-title", Static).update(
            "Welcome to Battlecode CLI"
            if not profiles and not credential and not self.api.demo
            else "API keys & accounts"
        )
        self.query_one("#connection-summary", Static).update(redact(state))
        self.fill(
            self.query_one("#accounts-table", DataTable),
            [
                (
                    a.id,
                    (
                        a.label,
                        a.team_name,
                        "ACTIVE" if a.id == active_id else "Saved",
                        "OS keyring" if a.storage == "keyring" else "Private file (unencrypted)",
                    ),
                )
                for a in profiles
            ],
        )
        self.saved_profile_count = len(profiles)
        self.query_one("#accounts-table").display = bool(profiles)
        self.query_one("#saved-keys-title").display = bool(profiles)
        self.query_one("#accounts-empty").display = False
        for ident in ("use-account", "delete-account", "disconnect-account"):
            self.query_one(f"#{ident}").display = bool(profiles) or credential is not None
        self.query_one("#local-paths", Static).update(
            f"Config: {config_dir()}\nDownloads: {download_dir()}\nReplay library: {self.library.root}\nAuto-refresh: {self.refresh_seconds}s"
        )
        for ident in ("connect-key", "save-key"):
            self.query_one(f"#{ident}", Button).disabled = self.api.demo or self.auth_changing
        self.query_one("#import-key", Button).disabled = (
            self.api.demo or self.auth_changing or self.importable is None
        )
        self.query_one("#disconnect-account", Button).disabled = (
            self.api.demo or self.auth_changing or credential is None
        )
        valid = any(a.id == self.account_id for a in profiles)
        self.query_one("#delete-account", Button).disabled = (
            self.api.demo or self.auth_changing or not valid
        )
        current = credential.profile_id if credential else None
        self.query_one("#use-account", Button).disabled = (
            self.api.demo
            or self.auth_changing
            or not valid
            or (self.account_id == current and not self.auth_failed)
        )

    def action_view(self, page: str) -> None:
        requested = page
        page = {
            "upload": "bots",
            "replays": "games",
            "challenge": "arena",
            "ladder": "leaderboard",
        }.get(page, page)
        if page not in dict(PAGES) or len(self.screen_stack) > 1:
            return
        self.query_one("#pages", ContentSwitcher).current = page
        if requested in ("bots", "upload"):
            self.query_one("#bot-tabs", TabbedContent).active = (
                "bot-upload" if requested == "upload" else "bot-versions"
            )
        if requested in ("games", "replays"):
            self.query_one("#game-tabs", TabbedContent).active = (
                "game-local" if requested == "replays" else "game-matches"
            )
        if requested == "challenge":
            self.query_one("#arena-tabs", TabbedContent).active = "arena-online"
        self.query_one("#nav", OptionList).highlighted = [p for p, _ in PAGES].index(page)
        focus = {
            "overview": "overview-games",
            "bots": "bots-table",
            "games": "games-table",
            "leaderboard": "ladder-search",
            "arena": "arena-bot-a",
            "settings": "accounts-table" if self.saved_profile_count else "api-key",
        }
        target = {
            "upload": "upload-path",
            "challenge": "challenge-team",
            "replays": "replay-path",
        }.get(requested, focus[page])
        tabs_id = {"bots": "bot-tabs", "games": "game-tabs", "arena": "arena-tabs"}.get(page)
        pane = self.query_one(f"#{tabs_id}", TabbedContent).active if tabs_id else None
        if page == "arena":
            target = {
                "arena-local": "arena-bot-a",
                "arena-online": "challenge-team",
                "arena-results": "arena-results-table",
            }[pane]

        def focus_current():
            if self.query_one("#pages", ContentSwitcher).current == page and (
                not tabs_id or self.query_one(f"#{tabs_id}", TabbedContent).active == pane
            ):
                self.query_one(f"#{target}").focus()

        self.call_after_refresh(focus_current)
        if (self.api.demo or self.api.credential) and (
            (page == "leaderboard" and not self.ladder)
            or (requested == "challenge" and not self.maps)
        ):
            self.refresh_data()
        if requested == "replays":
            self.render_library()
        if page == "arena":
            self.update_arena_runner()
        if page == "settings":
            self.update_settings()

    @on(OptionList.OptionSelected, "#nav")
    def nav_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_view(str(event.option.id))

    def action_help(self) -> None:
        self.push_screen(TextViewer("Keyboard guide", HELP))

    def account_is_current(self) -> bool:
        if self.auth_changing:
            return False
        if self.api.demo or self.custom_api or not self.api.credential:
            return True
        try:
            current = self.accounts.active_id == self.api.credential.profile_id
        except (ValueError, OSError):
            current = False
        if not current:
            self.auth_changing = True
            self.detach_changed_account()
        return current

    @work(group="auth")
    async def detach_changed_account(self) -> None:
        try:
            await self.replace_api(BattlecodeAPI(None))
            message = "API-key selection changed outside this dashboard. The previous connection was cleared. Reconnect a saved key in Settings."
            self.query_one("#settings-status", Static).update(message)
            self.status(message)
            self.action_view("settings")
        finally:
            self.auth_changing = False
            self.update_settings()

    def require_same_account(self, api) -> None:
        if not self.account_is_current() or api is not self.api:
            raise APIError(
                "Account changed while this action was open. Nothing was sent. Reconnect and review the action again."
            )

    def poll(self) -> None:
        if not self.account_is_current():
            return
        if (
            (self.api.demo or self.api.credential)
            and not self.auth_failed
            and not self.syncing
            and not self.mutating
            and time.monotonic() >= self.cooldown_until
        ):
            self.refresh_data()

    def action_refresh(self) -> None:
        self.render_library()
        if self.syncing or self.auth_changing:
            return
        if time.monotonic() < self.cooldown_until:
            self.status("Rate limited. Waiting for the server's retry window.")
            return
        self.auth_failed = False
        self.last_ladder_fetch = 0
        self.refresh_data()

    @work(group="sync", exclusive=True)
    async def refresh_data(self) -> None:
        if not self.account_is_current():
            return
        if not self.api.demo and not self.api.credential:
            self.status("Offline. Connect in API keys (6), or view local replays in Games.")
            return
        self.syncing = True
        self.status("Refreshing…" if self.team else "Connecting to Battlecode…")
        endpoints = ["/team", "/submissions", "/battles?limit=50"]
        if not self.ladder or time.monotonic() - self.last_ladder_fetch >= self.refresh_seconds:
            endpoints.append("/leaderboard")
        if not self.maps:
            endpoints.append("/maps")
        try:
            results = await asyncio.gather(
                *(self.api.get(p) for p in endpoints), return_exceptions=True
            )
            if not self.account_is_current():
                return
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
                    self.ladder = leaderboard_rows(result)
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
        old_keys = list(table.rows)
        old_key = str(old_keys[min(old_row, len(old_keys) - 1)].value) if old_keys else None
        table.clear()
        for key, cells in data:
            table.add_row(*[Text(redact(str(value))) for value in cells], key=key)
        if data:
            keys = [key for key, _ in data]
            table.move_cursor(
                row=keys.index(old_key) if old_key in keys else min(old_row, len(data) - 1)
            )

    def render_data(self) -> None:
        team = team_data(self.team)
        active = active_bot(self.submissions)
        name = redact(str(team.get("name", "Your team")))
        self.query_one("#team-summary", Static).update(Text(name, style="bold"))
        standing = next((entry for entry in self.ladder if entry.get("id") == team.get("id")), {})
        rating = number(team.get("elo", team.get("rating", standing.get("elo"))))
        rank = number(self.team.get("rank", team.get("rank", standing.get("rank"))))
        self.query_one("#elo-value", Digits).update(f"{rating:g}" if rating is not None else "--")
        self.query_one("#rank-value", Digits).update(f"{rank:g}" if rank is not None else "--")
        self.query_one("#team-record", Static).update(
            f"{record_label(team)}\n{winrate(team)} win rate"
        )
        elo_points, rank_points, inferred = histories(team, self.ladder)
        if not self.api.demo and self.team:
            try:
                observed = self.history_store.observe(team, rank)
                if len(elo_points) < 2:
                    elo_points = series(observed, "elo")
                if len(rank_points) < 2:
                    rank_points, inferred = series(observed, "rank"), False
            except OSError:
                self.status("Could not save local history. Server data is still available.")
        self.query_one("#elo-history", HistoryChart).set_series(elo_points)
        self.query_one("#rank-history", HistoryChart).set_series(rank_points, inferred=inferred)
        self.query_one("#active-summary", Static).update(
            f"ACTIVE  {active['name']}   ·   {winrate(active)} wins   ·   {record_label(active)}"
            if active
            else "No active bot"
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
        self.render_games()
        self.render_ladder()
        if self.bot_id:
            bot = next((s for s in self.submissions if s["id"] == self.bot_id), None)
            if bot:
                self.display_bot(bot)

    def filtered_battles(self) -> list[dict]:
        mode = self.query_one("#games-filter", Select).value
        normalized = [battle_row(b, team_data(self.team).get("id")) for b in self.battles]
        return [
            b for b in normalized if mode == "all" or (b["mode"] == "Ranked") == (mode == "ranked")
        ]

    @on(Select.Changed, "#games-filter")
    def games_filter_changed(self) -> None:
        if self.query_one("#games-table", DataTable).columns:
            self.render_games()

    def render_games(self) -> None:
        normalized = self.filtered_battles()
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

    def render_ladder(self) -> None:
        search = self.query_one("#ladder-search", Input).value.casefold()
        selected = [
            (i, t)
            for i, t in enumerate(self.ladder, 1)
            if search in str(t.get("name", "")).casefold()
            or search in str(t.get("id", t.get("teamId", "")))
            or search in members_label(t).casefold()
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
                        winrate(t),
                        t.get("wins", (t.get("record") or {}).get("wins", "n/a")),
                        members_label(t),
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
            else "Connect an API key to load live standings."
        )
        self.query_one("#leaderboard-state", Static).update(
            f"{len(self.ladder)} teams · {len(selected)} shown · {'synthetic demo' if self.api.demo else 'live API'} · win rate n/a when records are unavailable"
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
        for ident in ("arena-use-a", "arena-use-b"):
            self.query_one(f"#{ident}", Button).disabled = self.api.demo

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
        if not self.account_is_current():
            return
        ident = self.bot_id
        if ident is None:
            return
        try:
            raw = await self.api.get(f"/submissions/{ident}")
            detail = raw.get("submission", raw)
            if self.bot_id != ident or not self.account_is_current():
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
        normalized = self.filtered_battles()
        index = next((i for i, b in enumerate(normalized) if b["id"] == self.battle_id), 0)
        self.query_one("#games-table", DataTable).move_cursor(row=index)
        self.inspect_battle()

    @work(group="game-detail", exclusive=True)
    async def inspect_battle(self, focus: bool = True) -> None:
        if not self.account_is_current():
            return
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
            if self.battle_id != ident or not self.account_is_current():
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
        if self.mutating or self.auth_changing:
            self.notify("Another action is still running. Wait for it to finish.")
            return False
        if not self.account_is_current():
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
        api = self.api
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
                self.require_same_account(api)
                self.status("Activating bot…")
                await api.activate(bot["id"])
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
        api = self.api
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
            self.require_same_account(api)
            self.query_one("#upload-status", Static).update("Uploading once. Please wait…")
            reply = await api.upload(name, description, bot.language, bot.blob)
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
                "Enter an opponent team ID, or choose one on the Leaderboard."
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
        api = self.api
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
            self.require_same_account(api)
            reply = await api.challenge(ident, ranked, maps)
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

    @on(DataTable.RowHighlighted, "#accounts-table")
    def account_highlight(self, event: DataTable.RowHighlighted) -> None:
        use = self.query_one_optional("#use-account", Button)
        delete = self.query_one_optional("#delete-account", Button)
        # A table can deliver queued cursor events while sibling controls are unmounting.
        if use is None or delete is None:
            return
        self.account_id = str(event.row_key.value)
        current = self.api.credential.profile_id if self.api.credential else None
        use.disabled = (
            self.api.demo
            or self.auth_changing
            or (self.account_id == current and not self.auth_failed)
        )
        delete.disabled = self.api.demo or self.auth_changing

    @on(Input.Submitted, "#api-key")
    @on(Input.Submitted, "#account-label")
    def key_submitted(self, event: Input.Submitted) -> None:
        self.connect_key()

    def begin_account_change(self) -> bool:
        if self.api.demo or self.mutating or self.auth_changing:
            self.notify(
                "API-key changes are unavailable in demo mode or while another action is running."
            )
            return False
        self.mutating = self.auth_changing = True
        for group in ("sync", "bot-detail", "game-detail", "download"):
            self.workers.cancel_group(self, group)
        self.update_settings()
        return True

    async def replace_api(self, api) -> None:
        for group in ("sync", "bot-detail", "game-detail", "download"):
            self.workers.cancel_group(self, group)
        await asyncio.sleep(0)
        old = self.api
        self.api = api
        await old.close()
        self.reset_account()
        self.store_error = ""
        self.query_one("#connection", Static).update("CONNECTED" if api.credential else "OFFLINE")
        self.update_settings()

    @work(group="auth")
    async def connect_key(self, *, connect: bool = True, imported: bool = False) -> None:
        field = self.query_one("#api-key", Input)
        token = self.importable.token if imported and self.importable else field.value.strip()
        field.value = ""
        if not KEY_PATTERN.fullmatch(token):
            self.query_one("#settings-status", Static).update(
                "Paste a valid API key from your team page, starting with bc_."
            )
            return
        if not self.begin_account_change():
            return
        candidate = BattlecodeAPI(Credential(token, "verifying key"))
        installed = False
        try:
            self.query_one("#settings-status", Static).update("Verifying your key…")
            who = await candidate.get("/me")
            label = self.query_one("#account-label", Input).value.strip()
            account = await asyncio.to_thread(self.accounts.add, token, label, who, activate=False)
            if (
                imported
                and self.importable
                and self.importable.source == str(config_dir() / "credentials.json")
            ):
                (config_dir() / "credentials.json").unlink(missing_ok=True)
                self.importable = importable_credential()
            if connect and self.api.credential and self.api.credential.profile_id != account.id:
                connect = bool(
                    await self.push_screen_wait(
                        Confirm(
                            "Switch connected account?",
                            f"Connect {account.label} ({account.team_name})?\n\nThe current account is disconnected and its cached dashboard data is cleared. Only one API key can be active. Saved keys and local replay files are kept.",
                            "Switch account",
                        )
                    )
                )
            if connect:
                credential = await asyncio.to_thread(self.accounts.activate, account.id)
                candidate.credential = credential
                await self.replace_api(candidate)
                installed = True
                self.query_one("#settings-status", Static).update(
                    f"Connected to {account.team_name}. This is the only active API key."
                )
            else:
                self.query_one("#settings-status", Static).update(
                    f"Saved {account.label}. Not connected; choose it in the table and click Use selected."
                )
        except Exception as error:
            self.fail(APIError(redact(str(error), token)), "#settings-status")
        finally:
            if not installed:
                await candidate.close()
            self.mutating = self.auth_changing = False
            self.update_settings()
            if installed:
                self.refresh_data()

    @work(group="auth")
    async def use_account(self) -> None:
        identifier = self.account_id
        if not identifier or not self.begin_account_change():
            return
        candidate = None
        installed = False
        try:
            profile = self.accounts.resolve(identifier)
            if self.api.credential and self.api.credential.profile_id != identifier:
                if not await self.push_screen_wait(
                    Confirm(
                        "Switch connected account?",
                        f"Connect {profile.label} ({profile.team_name})? The current account will be disconnected and its dashboard data cleared.",
                        "Switch account",
                    )
                ):
                    return
            credential = await asyncio.to_thread(self.accounts.credential, identifier)
            candidate = BattlecodeAPI(credential)
            await candidate.get("/me")
            await asyncio.to_thread(self.accounts.activate, identifier)
            await self.replace_api(candidate)
            installed = True
            self.query_one("#settings-status", Static).update(
                f"Connected to {profile.team_name}. Other keys remain saved but disconnected."
            )
        except Exception as error:
            self.fail(error, "#settings-status")
        finally:
            if candidate and not installed:
                await candidate.close()
            self.mutating = self.auth_changing = False
            self.update_settings()
            if installed:
                self.refresh_data()

    @work(group="auth")
    async def disconnect_account(self) -> None:
        if not self.begin_account_change():
            return
        try:
            await asyncio.to_thread(self.accounts.disconnect)
            await self.replace_api(BattlecodeAPI(None))
            self.query_one("#settings-status", Static).update(
                "Disconnected. Your keys are still saved. No other key will connect automatically."
            )
            self.status("Offline. Local replay viewing remains available.")
        except Exception as error:
            self.fail(error, "#settings-status")
        finally:
            self.mutating = self.auth_changing = False
            self.update_settings()

    @work(group="auth")
    async def delete_account(self) -> None:
        identifier = self.account_id
        if not identifier or not self.begin_account_change():
            return
        try:
            account = self.accounts.resolve(identifier)
            if not await self.push_screen_wait(
                Confirm(
                    "Delete this saved API key?",
                    f"Remove {account.label} ({account.team_name}) from this app?\n\nIf connected, it will be disconnected. This deletes the local secret, not the Battlecode account or server key. Revoke the key on your team page to disable it everywhere.",
                    "Delete key",
                )
            ):
                return
            if self.accounts.active_id == identifier or (
                self.api.credential and self.api.credential.profile_id == identifier
            ):
                await asyncio.to_thread(self.accounts.disconnect)
                await self.replace_api(BattlecodeAPI(None))
            await asyncio.to_thread(self.accounts.delete, identifier)
            self.account_id = None
            self.query_one("#settings-status", Static).update(
                "Saved key deleted. No other account was connected."
            )
        except Exception as error:
            self.fail(error, "#settings-status")
        finally:
            self.mutating = self.auth_changing = False
            self.update_settings()

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
            "arena-use-a",
            "arena-use-b",
            "download-replay",
            "open-replay",
            "view-game",
            "game-log",
        ):
            self.query_one(f"#{ident}", Button).disabled = True
        self.render_data()

    @work(group="download", exclusive=True)
    async def download_bot(self) -> None:
        if not self.account_is_current() or self.bot_id is None:
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
        if not self.account_is_current() or self.game_id is None:
            return
        ident = self.game_id
        try:
            self.status(f"Downloading replay #{ident}…")
            path = await self.api.download(
                f"/battles/{ident}/replay", download_dir() / f"{ident}.replay"
            )
            self.status(f"Saved {path}")
            if open_after:
                self.open_replay_file(str(path), import_file=True)
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
        if ident == "projects-link":
            webbrowser.open("https://github.com/sebastianmiletic/battlecode-cli")
        elif ident == "leaderboard-refresh":
            self.action_refresh()
        elif ident in ("home-upload", "bots-upload"):
            self.action_view("upload")
        elif ident == "home-challenge":
            self.action_view("challenge")
        elif ident in ("home-replays", "continue-offline"):
            self.action_view("replays")
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
        elif ident in ("connect-key", "save-key", "import-key"):
            self.connect_key(connect=ident != "save-key", imported=ident == "import-key")
        elif ident == "use-account":
            self.use_account()
        elif ident == "disconnect-account":
            self.disconnect_account()
        elif ident == "delete-account":
            self.delete_account()
        elif ident == "browse-replay":
            self.push_screen(
                FilePicker(self.query_one("#replay-path", Input).value, kind="replay"),
                self.picked_replay,
            )
        elif ident == "import-replay":
            self.open_replay_file(self.query_one("#replay-path", Input).value, import_file=True)
        elif ident == "watch-replay" and self.local_replay_id:
            self.open_replay_file(str(self.library.path(self.local_replay_id)), import_file=False)
        elif ident == "remove-replay":
            self.remove_local_replay()
        elif ident == "sample-replay":
            replay = decode_replay(
                files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
            )
            self.push_screen(ReplayViewer(replay, "Synthetic sample"))
        elif ident == "team-page":
            webbrowser.open(f"{SERVER}/team")

    def render_library(self) -> None:
        try:
            self.local_replays = self.library.entries()
            self.fill(
                self.query_one("#replays-table", DataTable),
                [
                    (
                        e["id"],
                        (
                            e["name"],
                            e["map"],
                            e["rounds"],
                            e["winner"],
                            date_label(e["imported_at"]),
                        ),
                    )
                    for e in self.local_replays
                ],
            )
            self.query_one("#replays-empty").display = not bool(self.local_replays)
            if not self.local_replays:
                self.local_replay_id = None
                self.query_one("#watch-replay", Button).disabled = True
                self.query_one("#remove-replay", Button).disabled = True
        except Exception as error:
            self.query_one("#replays-status", Static).update(redact(str(error)))

    @on(DataTable.RowHighlighted, "#replays-table")
    def replay_highlight(self, event: DataTable.RowHighlighted) -> None:
        watch = self.query_one_optional("#watch-replay", Button)
        remove = self.query_one_optional("#remove-replay", Button)
        if watch is None or remove is None:
            return
        self.local_replay_id = str(event.row_key.value)
        watch.disabled = remove.disabled = False

    @on(DataTable.RowSelected, "#replays-table")
    def replay_selected(self, event: DataTable.RowSelected) -> None:
        self.local_replay_id = str(event.row_key.value)
        self.open_replay_file(str(self.library.path(self.local_replay_id)), import_file=False)

    @work(group="replay", exclusive=True)
    async def open_replay_file(self, value: str, *, import_file: bool) -> None:
        if not value.strip():
            self.query_one("#replays-status", Static).update("Choose a local replay file first.")
            return
        try:
            self.status("Decoding local replay. No files are uploaded to the server…")
            if import_file:
                entry, replay = await asyncio.to_thread(self.library.import_file, value)
                self.local_replay_id = entry["id"]
                self.render_library()
            else:
                replay = await asyncio.to_thread(load_replay, value)
            self.query_one("#replays-status", Static).update(
                "Replay ready. Files remain on this computer."
            )
            self.status("Local replay loaded. No API key was used to decode it.")
            self.push_screen(ReplayViewer(replay, Path(value.strip().strip("\"'")).name))
        except Exception as error:
            self.fail(error, "#replays-status")

    @work(group="replay")
    async def remove_local_replay(self) -> None:
        identifier = self.local_replay_id
        entry = next((e for e in self.local_replays if e["id"] == identifier), None)
        if not entry:
            return
        if await self.push_screen_wait(
            Confirm(
                "Remove local replay?",
                f"Delete this app's library copy of {entry['name']}? The original imported file and all server games are untouched.",
                "Remove replay",
            )
        ):
            try:
                await asyncio.to_thread(self.library.remove, identifier)
                self.local_replay_id = None
                self.render_library()
            except Exception as error:
                self.fail(error, "#replays-status")

    def picked_replay(self, path: str | None) -> None:
        if path:
            self.query_one("#replay-path", Input).value = path

    def picked_file(self, path: str | None) -> None:
        if path:
            self.query_one("#upload-path", Input).value = path
            name = self.query_one("#upload-name", Input)
            if not name.value:
                name.value = Path(path.strip().strip("\"'")).stem
