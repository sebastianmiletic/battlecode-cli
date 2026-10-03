"""Website-shaped TUI workflows, retaining the existing account and mutation boundaries."""

from __future__ import annotations

import asyncio
import json
import time
import webbrowser
from dataclasses import replace
from urllib.parse import urlsplit

from rich.text import Text
from textual import on, work
from textual.message_pump import MessagePump
from textual.widgets import (
    Button,
    DataTable,
    Digits,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    TabbedContent,
    Tree,
)
from textual.widgets.option_list import Option

from .api import APIError
from .charts import HistoryChart
from .config import SERVER, download_dir, redact
from .dialogs import FilePicker, TextViewer
from .history import histories, number
from .models import battle_row, date_label, members_label, rows, team_data, winrate
from .replay_viewer import ReplayViewer
from .replays import clean_label, load_replay
from .site import (
    DOCS,
    WebsiteClient,
    bundled_doc,
    directory_rows,
    named_record,
    public_matches,
    safe_url,
    tournament_rows,
)


class SiteActions(MessagePump):
    def init_site(self):
        self.website = WebsiteClient()
        self.site_cache = {}
        self.public_pages = {}
        self.public_offsets = {"battles": 0, "games": 0}
        self.teams = []
        self.tournaments = []
        self.rating_teams = []
        self.public_profile = None
        self.public_profile_id = None
        self.tournament_id = None
        self.current_doc = "overview"
        self.site_page = "overview"
        self.nav_indices = {}
        self.rating_id = "ours"
        self.current_member = {}
        self.simulated_matches = []

    def site_setup(self):
        columns = {
            "overview-ladder": ("RANK", "TEAM", "ELO", "Δ"),
            "my-battles-table": ("WHEN", "MODE", "OPPONENT", "RESULT", "GAMES", "Δ ELO", "BATTLE"),
            "public-battles-table": ("TIME", "TYPE", "TEAM A", "SCORE", "TEAM B", "BATTLE"),
            "public-games-table": ("TIME", "TYPE", "TEAM A", "RESULT", "TEAM B", "MAP", "GAME"),
            "ratings-table": ("RANK", "TEAM", "RATING", "PEAK", "MEMBERS"),
            "tournaments-table": ("TOURNAMENT", "STATUS", "WHEN", "TEAMS", "ID"),
            "teams-table": ("TEAM", "ELO", "RANK", "WIN RATE", "MEMBERS", "INSTITUTION", "ID"),
            "profile-battles": ("WHEN", "OPPONENT", "RESULT", "MODE", "Δ ELO"),
            "profile-members": ("MEMBER", "ROLE", "INSTITUTION"),
            "profile-requests": ("NAME", "REQUESTED", "STATUS"),
            "visualiser-replays": ("REPLAY", "MAP", "MODE", "ROUNDS", "WINNER"),
        }
        for ident, headings in columns.items():
            table = self.query_one("#" + ident, DataTable)
            table.add_columns(*headings)
            table.show_row_labels = False
        self.render_docs_topics()
        self.show_doc(self.current_doc)
        self.render_site_data()
        self.refresh_simulated_matches()

    def clear_site_account(self):
        self.site_cache.clear()
        self.teams = self.tournaments = self.rating_teams = []
        self.public_profile = self.public_profile_id = None
        self.tournament_id = None
        self.rating_id = "ours"
        self.current_member = {}
        self.fill(self.query_one("#tournaments-table", DataTable), [])
        self.query_one("#tournament-bracket", Tree).clear()
        self.query_one("#account-profile", Static).update("No account profile loaded.")
        self.query_one("#sidebar-account", Button).label = "Account / keys"
        self.query_one("#sidebar-quota", Static).update("Autoscrims this hour\nn/a")

    def site_title(self, page=None):
        page = page or self.site_page
        from .site import PAGES

        title = dict(PAGES).get(page, "Battlecode")
        own = team_data(self.team)
        if page == "overview":
            title = clean_label(own.get("name", "Overview"))
        elif page == "team":
            title = clean_label(team_data(self.public_profile or self.team).get("name", "Team"))
        self.query_one("#site-title", Static).update(Text(title, style="bold"))
        self.query_one("#site-eyebrow", Static).update(
            "Your team" if page == "overview" else "Team" if page == "team" else "Battlecode 2026"
        )

    def site_navigate(self, page):
        self.site_page = page
        self.site_title(page)
        if page in ("battles", "updates"):
            self.load_public_page(page)
        elif page == "games":
            self.load_public_page("games")
        elif page in ("ratings", "teams", "tournaments"):
            self.load_site_api(page)
        elif page == "documentation":
            self.show_doc(self.current_doc)
            if not self.api.demo:
                self.refresh_doc(force=False)
        elif page == "team":
            self.render_profile()
        elif page == "visualiser":
            self.render_site_library()
        elif page == "my-battles":
            self.render_my_battles()
        elif page == "my-games":
            self.refresh_simulated_matches()
        elif page == "settings":
            self.load_account_profile()

    @work(group="site-api", exclusive=True)
    async def load_site_api(self, page, *, force=False):
        endpoints = {
            "ratings": "/ratings",
            "teams": "/teams",
            "tournaments": "/tournaments",
            "queue": "/queue",
        }
        endpoint = endpoints[page]
        if not self.account_is_current():
            return
        if not self.api.demo and (not self.api.credential or self.auth_failed):
            self.site_state(
                page,
                "Connect a valid API key for this documented JSON feed. Public pages and local tools remain available.",
            )
            return
        cached = self.site_cache.get(endpoint)
        if cached and not force and time.monotonic() - cached[0] < 45:
            self.apply_site_api(page, cached[1])
            return
        if time.monotonic() < self.cooldown_until:
            self.site_state(page, "Rate limited. Waiting for the server's retry window.")
            return
        api = self.api
        try:
            self.site_state(page, "Loading…")
            raw = await api.get(endpoint)
            self.require_same_account(api)
            self.site_cache[endpoint] = (time.monotonic(), raw)
            self.apply_site_api(page, raw)
        except Exception as error:
            if isinstance(error, APIError):
                self.auth_failed |= error.status == 401
                if error.status == 429:
                    self.cooldown_until = time.monotonic() + error.retry_after
            self.site_state(page, str(error))
            self.status(str(error))

    def site_state(self, page, message):
        ident = {
            "ratings": "ratings-summary",
            "teams": "teams-state",
            "tournaments": "tournaments-state",
            "queue": "sidebar-quota",
        }[page]
        widget = self.query_one_optional("#" + ident, Static)
        if widget:
            widget.update(redact(message))

    def apply_site_api(self, page, raw):
        if page == "teams":
            self.teams = directory_rows(raw)
            self.render_teams()
        elif page == "ratings":
            from .models import leaderboard_rows

            self.rating_teams = leaderboard_rows(raw)
            self.render_ratings()
        elif page == "tournaments":
            self.tournaments = tournament_rows(raw)
            self.render_tournaments()
        elif page == "queue":
            source = raw.get("queue", raw) if isinstance(raw, dict) else {}
            capacity = source.get("capacity", source.get("workers"))
            queued = source.get("length", source.get("queued", source.get("pending")))
            self.query_one("#sidebar-quota", Static).update(
                f"Judge queue {queued if queued is not None else 'n/a'}\nCapacity {capacity if capacity is not None else 'n/a'}"
            )

    @work(group="site-public", exclusive=True)
    async def load_public_page(self, page, *, force=False):
        try:
            if self.api.demo:
                self.render_demo_public(page)
                return
            path = (
                "/updates" if page == "updates" else f"/{page}?page={self.public_offsets[page] + 1}"
            )
            result = await self.website.page(path, force=force)
            self.public_pages[page] = result
            if page == "updates":
                await self.query_one("#updates-document", Markdown).update(result.text)
            else:
                self.render_public_matches(page)
        except Exception as error:
            if page == "updates":
                await self.query_one("#updates-document", Markdown).update(
                    "# Updates\n\n" + clean_label(error, 600)
                )
            else:
                self.query_one(f"#public-{page}-state", Static).update(redact(str(error)))

    def render_demo_public(self, page):
        if page == "updates":
            self.query_one("#updates-document", Markdown).update(
                "# Updates\n\nSynthetic preview. Live announcements are fetched from the official public Updates page; no announcements are invented."
            )
            return
        table = self.query_one(f"#public-{page}-table", DataTable)
        columns = len(table.columns)
        data = []
        search = self.query_one(f"#public-{page}-search", Input).value.casefold()
        mode = self.query_one(f"#public-{page}-mode", Select).value
        for raw in self.battles:
            battle = battle_row(raw, team_data(self.team).get("id"))
            if search not in f"{battle['opponent']} {battle['id']}".casefold() or (
                mode != "all" and battle["mode"].lower() != mode
            ):
                continue
            cells = [
                battle["at"],
                battle["mode"],
                team_data(self.team).get("name", "Your team"),
                battle["score"],
                battle["opponent"],
                battle["id"],
            ]
            if page == "games":
                cells = [
                    battle["at"],
                    battle["mode"],
                    "Night shift",
                    battle["result"],
                    battle["opponent"],
                    "Synthetic series",
                    battle["id"],
                ]
            data.append((str(battle["id"]), tuple(cells[:columns])))
        self.fill(table, data)
        self.query_one(f"#public-{page}-empty").display = not data
        self.query_one(f"#public-{page}-state", Static).update(
            "Demo: synthetic preview, not global live records."
        )

    @work(group="site-account", exclusive=True)
    async def load_account_profile(self):
        if (
            self.auth_changing
            or self.auth_failed
            or (not self.api.demo and not self.api.credential)
        ):
            return
        api = self.api
        try:
            raw = await api.get("/me")
            self.require_same_account(api)
            member = raw.get("user", {}) if isinstance(raw, dict) else {}
            self.current_member = (
                {
                    key: member[key]
                    for key in ("id", "username", "name", "institution")
                    if key in member
                }
                if isinstance(member, dict)
                else {}
            )
            lines = [f"{key}: {clean_label(value)}" for key, value in self.current_member.items()]
            self.query_one("#account-profile", Static).update(
                "\n".join(lines) or "No account profile fields supplied."
            )
            self.query_one("#sidebar-account", Button).label = Text(
                clean_label(self.current_member.get("username", "Account / keys"))
            )
        except Exception as error:
            self.query_one("#account-profile", Static).update(redact(str(error)))

    def render_public_matches(self, page):
        snapshot = self.public_pages.get(page)
        if not snapshot:
            return
        records = public_matches(snapshot, page)
        search = self.query_one(f"#public-{page}-search", Input).value.casefold()
        mode = self.query_one(f"#public-{page}-mode", Select).value
        selected = []
        table = self.query_one(f"#public-{page}-table", DataTable)
        width = len(table.columns)
        for record in records:
            text = " ".join(record["cells"]).casefold()
            if search and search not in text and search not in str(record["id"]):
                continue
            if mode == "ranked" and ("unranked" in text or "ranked" not in text):
                continue
            if mode == "unranked" and "unranked" not in text:
                continue
            cells = (record["cells"] + ["·"] * width)[: width - 1] + [record["id"]]
            selected.append((str(record["id"]), tuple(cells)))
        self.fill(table, selected)
        self.query_one(f"#public-{page}-empty").display = not selected
        self.query_one(f"#public-{page}-state", Static).update(
            f"Public website snapshot · page {self.public_offsets[page] + 1} · {len(selected)} visible rows · fetched {date_label(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(snapshot.loaded_at)))}"
        )

    @on(Input.Changed, "#public-battles-search, #public-games-search")
    @on(Select.Changed, "#public-battles-mode, #public-games-mode")
    def public_filter_changed(self, event):
        page = "games" if "games" in event.control.id else "battles"
        if self.api.demo:
            self.render_demo_public(page)
        else:
            self.render_public_matches(page)

    def render_site_data(self):
        if self.query_one_optional("#overview-ladder", DataTable) is None:
            return
        team = team_data(self.team)
        own_index = next(
            (i for i, row in enumerate(self.ladder) if row.get("id") == team.get("id")), 0
        )
        if "autoscrimsThisHour" in self.team or "autoscrimsThisHour" in team:
            used = self.team.get("autoscrimsThisHour", team.get("autoscrimsThisHour"))
            self.query_one("#sidebar-quota", Static).update(
                f"Autoscrims this hour\n{clean_label(used)}"
            )
        self.query_one("#nearby-title", Static).update(
            "Ladder · around you"
            if any(row.get("id") == team.get("id") for row in self.ladder)
            else "Ladder · returned leaders"
        )
        near = self.ladder[max(0, own_index - 2) : max(0, own_index - 2) + 5]
        self.fill(
            self.query_one("#overview-ladder", DataTable),
            [
                (
                    str(row["id"]),
                    (
                        row.get("rank", i + 1),
                        row.get("name", "Team"),
                        row.get("elo", row.get("rating", "n/a")),
                        f"{row['elo'] - team['elo']:+g}"
                        if isinstance(row.get("elo"), (int, float))
                        and isinstance(team.get("elo"), (int, float))
                        else "·",
                    ),
                )
                for i, row in enumerate(near)
            ],
        )
        self.render_my_battles()
        self.render_profile()
        self.render_ratings()
        self.render_teams()
        self.render_site_library()
        self.site_title()

    @on(TabbedContent.TabActivated, "#game-tabs")
    def game_tab_activated(self, event):
        from textual.widgets import ContentSwitcher

        if self.query_one("#pages", ContentSwitcher).current != "games":
            return
        page = "games" if event.pane.id == "game-global" else "my-games"
        self.query_one("#nav", OptionList).highlighted = self.nav_indices[page]
        self.site_navigate(page)
        if page == "games":
            self.call_after_refresh(
                lambda: (
                    self.query_one("#public-games-search", Input).focus()
                    if self.site_page == "games"
                    else None
                )
            )

    @work(group="simulation-index", exclusive=True)
    async def refresh_simulated_matches(self):
        def collect():
            result = []
            for header in self.arena_store.entries():
                job = self.arena_store.load(header["id"])
                for game in reversed(job.get("games", [])):
                    summary = game.get("summary", {})
                    a = job["bots"][game["a"]]["label"]
                    b = job["bots"][game["b"]]["label"]
                    winner = summary.get("winner")
                    result.append(
                        {
                            "id": f"sim:{job['id']}:{game['index']}",
                            "display_id": f"S{game['index'] + 1}",
                            "opponent": f"{a} vs {b}",
                            "mode": "Simulation",
                            "result": "ERROR"
                            if game.get("error")
                            else a
                            if winner == "A"
                            else b
                            if winner == "B"
                            else "DRAW",
                            "score": game.get("map", "Map"),
                            "change": "·",
                            "at": date_label(job["created_at"]),
                            "job": job["id"],
                            "game": game,
                        }
                    )
                    if len(result) == 1000:
                        return result
            return result

        try:
            records = await asyncio.to_thread(collect)
            if self.is_running:
                self.simulated_matches = records
                self.render_games()
        except Exception as error:
            if self.is_running:
                self.status(f"Local Simulation index: {error}")

    @on(DataTable.RowHighlighted, "#games-table")
    def simulation_highlight(self, event):
        if self._closing or self.query_one_optional("#game-detail", Static) is None:
            return
        if not str(event.row_key.value).startswith("sim:"):
            return
        self.battle_id = self.game_id = None
        self.battle_details = {}
        self.fill(self.query_one("#game-parts", DataTable), [])
        self.query_one("#game-detail", Static).update(
            "Local Simulation · Enter opens its saved replay · no ELO change"
        )
        for name in ("download-replay", "open-replay", "view-game", "game-log"):
            self.query_one("#" + name, Button).disabled = True

    @work(group="simulation-replay", exclusive=True)
    async def open_simulated_match(self, key):
        try:
            row = next((row for row in self.simulated_matches if row["id"] == key), None)
            if not row:
                raise ValueError("Refresh the local Simulation index first.")
            game = row["game"]
            path = (
                self.library.path(game["library_id"])
                if game.get("library_id")
                else self.arena_store.path(row["job"]) / f"game-{game['index']:05d}.replay"
            )
            replay = await asyncio.to_thread(load_replay, path)
            names = game.get("summary", {}).get("bots", {})
            if names.get("A") and names.get("B"):
                replay = replace(replay, bots=(names["A"], names["B"]))
            self.push_screen(ReplayViewer(replay, "Simulation · " + game.get("map", "Map")))
        except Exception as error:
            self.fail(error)

    def render_my_battles(self):
        table = self.query_one_optional("#my-battles-table", DataTable)
        if table is None or not table.columns:
            return
        mode = self.query_one("#my-battles-filter", Select).value
        battles = [battle_row(raw, team_data(self.team).get("id")) for raw in self.battles]
        battles = [row for row in battles if mode == "all" or row["mode"].lower() == mode]
        self.fill(
            table,
            [
                (
                    str(row["id"]),
                    (
                        row["at"],
                        row["mode"],
                        row["opponent"],
                        row["result"],
                        row["score"],
                        row["change"],
                        row["id"],
                    ),
                )
                for row in battles
                if row["id"]
            ],
        )
        self.query_one("#my-battles-state", Static).update(
            f"{len(battles)} account series · select to inspect games and judge log"
        )

    @on(Select.Changed, "#my-battles-filter")
    def my_battles_filter_changed(self):
        self.render_my_battles()

    def render_teams(self):
        table = self.query_one_optional("#teams-table", DataTable)
        if table is None or not table.columns:
            return
        search = self.query_one("#teams-search", Input).value.casefold()
        kind = self.query_one("#teams-filter", Select).value
        selected = []
        source = self.teams or self.ladder
        for team in source:
            ident = team.get("id", team.get("teamId"))
            if not isinstance(ident, int) or ident <= 0:
                continue
            institution = team.get("institution", team.get("institutionName", "n/a"))
            if isinstance(institution, dict):
                institution = institution.get("name", "n/a")
            if (
                search
                not in f"{ident} {team.get('name', '')} {members_label(team)} {institution}".casefold()
            ):
                continue
            if kind == "eligible" and team.get("eligible", team.get("prizeEligible")) is not True:
                continue
            if kind == "members" and not team.get("members"):
                continue
            selected.append(
                (
                    str(ident),
                    (
                        team.get("name", "Team"),
                        team.get("elo", team.get("rating", "n/a")),
                        team.get("rank", "n/a"),
                        winrate(team),
                        members_label(team),
                        institution,
                        ident,
                    ),
                )
            )
        self.fill(table, selected)
        self.query_one("#teams-state", Static).update(
            f"{len(selected)} teams · {'directory feed' if self.teams else 'leaderboard fallback'} · private sources are not downloadable"
        )

    @on(Input.Changed, "#teams-search")
    @on(Select.Changed, "#teams-filter")
    def teams_filter_changed(self):
        self.render_teams()

    def render_ratings(self):
        table = self.query_one_optional("#ratings-table", DataTable)
        if table is None or not table.columns:
            return
        source = self.rating_teams or self.ladder
        search = self.query_one("#ratings-search", Input).value.casefold()
        selected = [
            row
            for row in source
            if search
            in f"{row.get('name', '')} {members_label(row)} {row.get('id', '')}".casefold()
        ]
        self.fill(
            table,
            [
                (
                    str(row["id"]),
                    (
                        row.get("rank", "n/a"),
                        row.get("name", "Team"),
                        row.get("elo", row.get("rating", "n/a")),
                        row.get("peak", row.get("peakElo", "n/a")),
                        members_label(row),
                    ),
                )
                for row in selected
                if isinstance(row.get("id"), int)
            ],
        )
        select = self.query_one("#ratings-team", Select)
        choices = [("Your team", "ours")] + [
            (str(row.get("name", "Team")), str(row["id"]))
            for row in selected
            if isinstance(row.get("id"), int)
        ]
        signature = tuple(choices)
        if getattr(self, "ratings_choices", None) != signature:
            current = select.value
            select.set_options(choices)
            select.value = current if current in [value for _, value in choices] else "ours"
            self.ratings_choices = signature
        self.update_rating_charts()

    @on(Input.Changed, "#ratings-search")
    def ratings_search_changed(self):
        self.render_ratings()

    @on(Select.Changed, "#ratings-team")
    def rating_selected(self, event):
        self.rating_id = event.value
        self.update_rating_charts()

    def update_rating_charts(self):
        source = self.rating_teams or self.ladder
        own = team_data(self.team)
        team = (
            own
            if self.rating_id == "ours"
            else next((row for row in source if str(row.get("id")) == str(self.rating_id)), own)
        )
        elo, ranks, inferred = histories(team, source)
        self.query_one("#ratings-elo", HistoryChart).set_series(elo)
        self.query_one("#ratings-rank", HistoryChart).set_series(ranks, inferred=inferred)
        self.query_one("#ratings-summary", Static).update(
            f"{team.get('name', 'Your team')} · ELO {team.get('elo', team.get('rating', 'n/a'))} · rank {team.get('rank', self.team.get('rank', 'n/a') if team is own else 'n/a')} · {'rank inferred from today’s eligible teams' if inferred else 'server history when supplied'}"
        )

    @work(group="site-profile", exclusive=True)
    async def open_public_team(self, ident):
        self.public_profile_id = int(ident)
        fallback = next(
            (row for row in (self.teams or self.ladder) if row.get("id") == int(ident)), {}
        )
        self.public_profile = {"team": fallback}
        self.action_view("team")
        if not self.api.demo and not self.api.credential:
            self.status("Connect an API key to load the team's documented public JSON profile.")
            return
        api = self.api
        try:
            result = await api.get(f"/teams/{int(ident)}")
            self.require_same_account(api)
            self.public_profile = result if isinstance(result, dict) else {"team": fallback}
            self.render_profile()
            self.site_title()
        except Exception as error:
            self.status(str(error))

    def render_profile(self):
        if self.query_one_optional("#profile-name", Static) is None:
            return
        raw = self.public_profile or self.team
        team = team_data(raw)
        self.query_one("#profile-name", Static).update(clean_label(team.get("name", "Your team")))
        self.query_one("#profile-bio", Static).update(
            clean_label(team.get("bio", team.get("description", "")), 600)
        )
        rating = number(team.get("elo", team.get("rating")))
        rank = number(raw.get("rank", team.get("rank")))
        self.query_one("#profile-elo", Digits).update(f"{rating:g}" if rating is not None else "--")
        self.query_one("#profile-rank", Digits).update(f"{rank:g}" if rank is not None else "--")
        peak = raw.get("peak", team.get("peak", team.get("peakElo", "n/a")))
        best = raw.get("bestRank", team.get("bestRank", "n/a"))
        self.query_one("#profile-peak", Static).update(f"{team.get('tier', '')}  Peak {peak}")
        self.query_one("#profile-best", Static).update(
            f"Best #{best}" if best != "n/a" else "Best n/a"
        )
        record = team.get("record") if isinstance(team.get("record"), dict) else team
        result = Text()
        for key, label, color in (
            ("wins", "W", "#137a38" if self.theme == "battlecode-site-light" else "#3ecf7a"),
            ("draws", "D", "#546171" if self.theme == "battlecode-site-light" else "#b4bac4"),
            ("losses", "L", "#b91c1c" if self.theme == "battlecode-site-light" else "#f0605a"),
        ):
            result.append(f"{record.get(key, 'n/a')} {label}  ", style="bold " + color)
        self.query_one("#profile-record", Static).update(result)
        self.query_one("#profile-winrate", Static).update(f"{winrate(team)} win rate")
        self.query_one("#profile-versus", Static).update(
            named_record(raw.get("against", raw.get("headToHead", {})))
        )
        elo, ranks, inferred = histories(team, self.ladder)
        self.query_one("#profile-elo-chart", HistoryChart).set_series(elo)
        self.query_one("#profile-rank-chart", HistoryChart).set_series(ranks, inferred=inferred)
        members = team.get("members", raw.get("members", []))
        if not isinstance(members, list):
            members = []
        member_rows = []
        for index, member in enumerate(members):
            member = member if isinstance(member, dict) else {"username": member}
            member_rows.append(
                (
                    str(index),
                    (
                        member.get("username", member.get("name", "Member")),
                        member.get(
                            "role",
                            "Leader"
                            if member.get("id") == team.get("leaderId")
                            and team.get("leaderId") is not None
                            else "Member",
                        ),
                        member.get("institution", "n/a"),
                    ),
                )
            )
        self.fill(self.query_one("#profile-members", DataTable), member_rows)
        self.query_one("#profile-members-title", Static).update(
            f"Members · {len(members)}"
            + (f" of {team['maxMembers']}" if team.get("maxMembers") else "")
        )
        recent = rows(raw, "battles", "recentBattles") or rows(team, "battles", "recentBattles")
        if not self.public_profile:
            recent = self.battles
        normalized = [battle_row(battle, team.get("id")) for battle in recent]
        self.fill(
            self.query_one("#profile-battles", DataTable),
            [
                (
                    str(row["id"]),
                    (row["at"], row["opponent"], row["result"], row["mode"], row["change"]),
                )
                for row in normalized[:20]
                if row["id"]
            ],
        )
        requests = rows(raw, "pendingJoinRequests", "joinRequests") or rows(
            team, "pendingJoinRequests", "joinRequests"
        )
        self.fill(
            self.query_one("#profile-requests", DataTable),
            [
                (
                    str(index),
                    (
                        request.get(
                            "username",
                            request.get(
                                "name", (request.get("user") or {}).get("username", "Member")
                            ),
                        ),
                        date_label(request.get("createdAt")),
                        request.get("status", "Pending"),
                    ),
                )
                for index, request in enumerate(requests)
            ],
        )
        eligibility = {
            key: team[key]
            for key in (
                "institution",
                "eligible",
                "prizeEligible",
                "ranked",
                "eligibility",
                "verified",
            )
            if key in team
        }
        self.query_one("#profile-eligibility", Static).update(
            redact(json.dumps(eligibility, indent=2, ensure_ascii=False))
            if eligibility
            else "No eligibility information supplied."
        )
        self.query_one("#profile-challenge", Button).disabled = (
            not self.public_profile_id or self.public_profile_id == team_data(self.team).get("id")
        )

    def render_tournaments(self):
        self.fill(
            self.query_one("#tournaments-table", DataTable),
            [
                (
                    str(row["id"]),
                    (
                        row.get("name", row.get("title", "Tournament")),
                        row.get("status", "n/a"),
                        date_label(row.get("startsAt", row.get("date"))),
                        row.get(
                            "teamCount",
                            len(row.get("teams", []))
                            if isinstance(row.get("teams"), list)
                            else "n/a",
                        ),
                        row["id"],
                    ),
                )
                for row in self.tournaments
                if isinstance(row.get("id"), int)
            ],
        )
        self.query_one("#tournaments-state", Static).update(
            f"{len(self.tournaments)} tournaments · official feed; select a bracket"
        )

    @work(group="site-tournament", exclusive=True)
    async def inspect_tournament(self, ident):
        api = self.api
        try:
            result = await api.get(f"/tournaments/{int(ident)}")
            self.require_same_account(api)
            tree = self.query_one("#tournament-bracket", Tree)
            tree.clear()
            tree.root.set_label(
                Text(clean_label(result.get("name", result.get("title", "Tournament"))))
            )
            count = 0

            def add(parent, value, depth=0):
                nonlocal count
                if depth > 12 or count >= 1500:
                    return
                items = (
                    value.items()
                    if isinstance(value, dict)
                    else enumerate(value)
                    if isinstance(value, list)
                    else []
                )
                for key, child in items:
                    if count >= 1500 or any(
                        secret in str(key).lower()
                        for secret in ("token", "secret", "password", "apikey")
                    ):
                        continue
                    count += 1
                    if isinstance(child, (dict, list)):
                        label = (
                            child.get("name", child.get("title", str(key)))
                            if isinstance(child, dict)
                            else str(key)
                        )
                        node = parent.add(
                            Text(clean_label(label, 120)),
                            data=child if isinstance(child, dict) else None,
                        )
                        add(node, child, depth + 1)
                    else:
                        parent.add_leaf(Text(f"{key}: {clean_label(child, 180)}"))

            add(tree.root, result)
            tree.root.expand_all()
        except Exception as error:
            self.site_state("tournaments", str(error))

    @on(Tree.NodeSelected, "#tournament-bracket")
    def bracket_match_selected(self, event):
        value = event.node.data
        if isinstance(value, dict):
            ident = value.get("battleId", value.get("matchId"))
            if type(ident) is int and ident > 0:
                self.show_site_battle(ident)

    def render_site_library(self):
        table = self.query_one_optional("#visualiser-replays", DataTable)
        if table is None or not table.columns:
            return
        self.fill(
            table,
            [
                (
                    entry["id"],
                    (
                        entry["name"],
                        entry["map"],
                        entry.get("mode", "Replay"),
                        entry["rounds"],
                        entry["winner"],
                    ),
                )
                for entry in self.local_replays
            ],
        )

    def render_docs_topics(self):
        search = self.query_one("#docs-search", Input).value.casefold()
        menu = self.query_one("#docs-topics", OptionList)
        previous = self.current_doc
        menu.clear_options()
        items = []
        for group, topics in DOCS:
            selected = []
            for slug, label in topics:
                document = bundled_doc(slug)
                if search in f"{label} {group} {document.text}".casefold():
                    selected.append((slug, label))
            if selected:
                items.append(Option(Text(group, style="dim"), disabled=True))
                items.extend(Option(label, id=slug) for slug, label in selected)
        menu.add_options(items)
        for index, option in enumerate(items):
            if option.id == previous:
                menu.highlighted = index
                break

    def show_doc(self, slug):
        self.current_doc = slug
        document = bundled_doc(slug)
        cached = self.website.cache.get("/docs/" + slug)
        self.query_one("#docs-document", Markdown).update(cached.text if cached else document.text)
        self.query_one("#docs-state", Static).update(
            "Cached official topic · refresh checks the current guide"
            if cached
            else "Offline technical summary · Refresh topic for the full current official guide"
        )

    @on(Input.Changed, "#docs-search")
    def docs_search_changed(self):
        self.render_docs_topics()

    @on(OptionList.OptionSelected, "#docs-topics")
    def doc_selected(self, event):
        if event.option.id:
            self.show_doc(str(event.option.id))
            if not self.api.demo:
                self.refresh_doc(force=False)

    @work(group="site-doc", exclusive=True)
    async def refresh_doc(self, *, force=True):
        slug = self.current_doc
        try:
            if self.api.demo:
                self.query_one("#docs-state", Static).update(
                    "Demo is offline. Open the official topic for the full current guide."
                )
                return
            document = await self.website.page("/docs/" + slug, force=force)
            if self.current_doc == slug:
                text = document.text
                marker = "\n# " + document.title
                if marker in "\n" + text:
                    text = ("\n" + text).split(marker, 1)[1]
                    text = "# " + document.title + text
                document.text = text
                await self.query_one("#docs-document", Markdown).update(text)
                self.query_one("#docs-state", Static).update(
                    "Current official guide · public HTML parsed as inert text"
                )
        except Exception as error:
            self.query_one("#docs-state", Static).update(redact(str(error)))

    @on(Markdown.LinkClicked)
    def guide_link(self, event):
        if url := safe_url(event.href):
            if "/docs/" in url:
                slug = urlsplit(url).path.split("/")[-1]
                if any(slug == topic for _, topics in DOCS for topic, _ in topics):
                    self.show_doc(slug)
                    self.refresh_doc(force=False)
                    return
            webbrowser.open(url)

    def selected_site_id(self, table_id):
        table = self.query_one("#" + table_id, DataTable)
        if table.row_count:
            key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            return str(key.value)
        return None

    @on(DataTable.RowSelected, "#teams-table, #overview-ladder, #ratings-table")
    def team_row_selected(self, event):
        self.open_public_team(int(str(event.row_key.value)))

    @on(DataTable.RowSelected, "#my-battles-table, #profile-battles, #public-battles-table")
    def site_battle_selected(self, event):
        self.show_site_battle(int(str(event.row_key.value)))

    @on(DataTable.RowSelected, "#public-games-table")
    def site_game_selected(self, event):
        self.open_public_game(int(str(event.row_key.value)))

    @on(DataTable.RowSelected, "#visualiser-replays")
    def visualiser_replay_selected(self, event):
        self.local_replay_id = str(event.row_key.value)
        self.open_replay_file(str(self.library.path(self.local_replay_id)), import_file=False)

    @on(DataTable.RowSelected, "#tournaments-table")
    def tournament_selected(self, event):
        self.inspect_tournament(int(str(event.row_key.value)))

    @on(
        DataTable.RowHighlighted,
        "#teams-table, #tournaments-table, #my-battles-table, #public-battles-table, #public-games-table",
    )
    def site_highlight(self, event):
        buttons = {
            "teams-table": ("teams-profile", "teams-challenge"),
            "tournaments-table": ("tournament-inspect",),
            "my-battles-table": ("my-battles-inspect",),
            "public-battles-table": ("public-battles-inspect",),
            "public-games-table": ("public-games-inspect",),
        }
        for ident in buttons.get(event.data_table.id, ()):
            button = self.query_one_optional("#" + ident, Button)
            if button:
                button.disabled = False

    def show_site_battle(self, ident):
        self.battle_id = int(ident)
        self.action_view("my-games")
        self.inspect_battle()

    @work(group="site-game")
    async def open_public_game(self, ident):
        if self.api.demo:
            self.show_site_battle(ident)
            return
        if not self.api.credential:
            self.action_view("settings")
            self.status(
                "A valid API key is required to download a server replay. Local replays work offline."
            )
            return
        api = self.api
        try:
            self.require_same_account(api)
            destination = download_dir() / f"game-{int(ident)}.replay"
            await api.download(
                f"/battles/{int(ident)}/replay", destination, max_bytes=64 * 1024 * 1024
            )
            self.require_same_account(api)
            self.open_replay_file(str(destination), import_file=True)
        except Exception as error:
            self.fail(error)

    @on(Button.Pressed)
    def site_buttons(self, event):
        ident = event.button.id or ""
        handled = True
        if ident in ("overview-submissions", "overview-battles", "overview-leaderboard"):
            self.action_view(
                {
                    "overview-submissions": "bots",
                    "overview-battles": "my-battles",
                    "overview-leaderboard": "leaderboard",
                }[ident]
            )
        elif ident == "sidebar-queue":
            self.load_site_api("queue", force=True)
        elif ident == "ladder-profile":
            if selected := self.selected_site_id("ladder-table"):
                self.open_public_team(int(selected))
        elif ident == "account-web":
            webbrowser.open(SERVER + "/profile")
        elif ident == "profile-star":
            webbrowser.open(
                SERVER + (f"/teams/{self.public_profile_id}" if self.public_profile_id else "/team")
            )
        elif ident == "site-challenge":
            self.action_view("challenge")
        elif ident == "sidebar-account":
            self.action_view("settings")
        elif ident == "sidebar-collapse":
            self.action_toggle_sidebar()
        elif ident == "sidebar-theme":
            self.action_toggle_site_theme()
        elif ident == "sidebar-discord":
            webbrowser.open("https://discord.gg/j5zwCXq9yw")
        elif ident == "sidebar-sponsors":
            self.push_screen(
                TextViewer(
                    "Presented by",
                    "Battlecode website sponsors:\nhttps://www.jumptrading.com/\nhttps://www.hudsonrivertrading.com/\n\nThis unofficial client is not endorsed by either sponsor.",
                )
            )
        elif ident.startswith("public-"):
            kind = "games" if ident.startswith("public-games") else "battles"
            action = ident.rsplit("-", 1)[1]
            if action in ("previous", "next"):
                self.public_offsets[kind] = max(
                    0, self.public_offsets[kind] + (-1 if action == "previous" else 1)
                )
                self.load_public_page(kind, force=True)
            elif action == "refresh":
                self.load_public_page(kind, force=True)
            elif action == "web":
                webbrowser.open(SERVER + "/" + kind)
            elif action == "inspect" and (
                selected := self.selected_site_id("public-" + kind + "-table")
            ):
                if kind == "games":
                    self.open_public_game(int(selected))
                else:
                    self.show_site_battle(int(selected))
        elif ident in ("updates-refresh", "updates-web"):
            if ident.endswith("refresh"):
                self.load_public_page("updates", force=True)
            else:
                webbrowser.open(SERVER + "/updates")
        elif ident in ("ratings-refresh", "teams-refresh", "tournaments-refresh"):
            self.load_site_api(ident.split("-")[0], force=True)
        elif ident in ("ratings-profile", "teams-profile", "teams-challenge"):
            table = "ratings-table" if ident.startswith("ratings") else "teams-table"
            selected = self.selected_site_id(table)
            if selected:
                if ident.endswith("challenge"):
                    self.prepare_challenge(int(selected))
                else:
                    self.open_public_team(int(selected))
        elif ident == "tournament-inspect":
            if selected := self.selected_site_id("tournaments-table"):
                self.inspect_tournament(int(selected))
        elif ident == "tournaments-web":
            webbrowser.open(SERVER + "/tournaments")
        elif ident == "teams-web":
            webbrowser.open(SERVER + "/teams")
        elif ident == "my-battles-inspect":
            if selected := self.selected_site_id("my-battles-table"):
                self.show_site_battle(int(selected))
        elif ident == "my-battles-refresh":
            self.action_refresh()
        elif ident == "my-battles-challenge":
            self.action_view("challenge")
        elif ident == "profile-own":
            self.public_profile = self.public_profile_id = None
            self.render_profile()
            self.site_title()
        elif ident == "profile-keys":
            self.action_view("settings")
        elif ident == "profile-challenge" and self.public_profile_id:
            self.prepare_challenge(self.public_profile_id)
        elif ident == "profile-manage":
            webbrowser.open(
                SERVER + (f"/teams/{self.public_profile_id}" if self.public_profile_id else "/team")
            )
        elif ident == "docs-refresh":
            self.refresh_doc()
        elif ident == "docs-web":
            webbrowser.open(SERVER + "/docs/" + self.current_doc)
        elif ident == "visualiser-browse":
            self.push_screen(
                FilePicker(self.query_one("#visualiser-path", Input).value, kind="replay"),
                self.visualiser_picked,
            )
        elif ident == "visualiser-open":
            self.open_replay_file(
                self.query_one("#visualiser-path", Input).value, import_file=False
            )
        elif ident == "visualiser-library":
            self.action_view("replays")
        elif ident == "visualiser-results":
            self.action_view("arena")
            self.query_one("#arena-tabs", TabbedContent).active = "arena-results"
        elif ident == "visualiser-sample":
            from importlib.resources import files

            from .replay_viewer import ReplayViewer
            from .replays import decode_replay

            self.push_screen(
                ReplayViewer(
                    decode_replay(
                        files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
                    ),
                    "Synthetic sample",
                )
            )
        else:
            handled = False
        if handled:
            event.stop()

    def visualiser_picked(self, value):
        if value:
            self.query_one("#visualiser-path", Input).value = value

    def action_toggle_sidebar(self):
        self.toggle_class("sidebar-collapsed")
        self.query_one("#sidebar-collapse", Button).label = (
            "›" if self.has_class("sidebar-collapsed") else "‹"
        )

    def action_toggle_site_theme(self):
        self.theme = (
            "battlecode-site-light" if self.theme == "battlecode-site" else "battlecode-site"
        )
        self.render_profile()
        for chart in self.query(HistoryChart):
            chart.refresh()

    def action_doc_search(self):
        if len(self.screen_stack) > 1:
            return
        self.action_view("documentation")
        self.query_one("#docs-search", Input).focus()
