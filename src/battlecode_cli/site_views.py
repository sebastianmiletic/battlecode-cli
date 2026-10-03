"""Native work surfaces following the saved Battlecode website's navigation."""

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from .charts import BoldDigits, HistoryChart
from .site import DOCS
from .views import ActionBar


class PublicMatches(Vertical):
    def __init__(self, kind: str, **kwargs):
        super().__init__(**kwargs)
        self.kind = kind

    def compose(self) -> ComposeResult:
        prefix = "public-" + self.kind
        with Horizontal(classes="site-filter-row"):
            yield Input(placeholder="Search teams or battle ID", id=prefix + "-search")
            yield Select(
                [("All modes", "all"), ("Ranked", "ranked"), ("Unranked", "unranked")],
                value="all",
                allow_blank=False,
                id=prefix + "-mode",
            )
        yield Static(
            "Public website snapshot; your private logs are only fetched through the account API.",
            id=prefix + "-state",
            classes="hint",
            markup=False,
        )
        yield DataTable(id=prefix + "-table", cursor_type="row")
        yield Static(
            "Refresh to load public matches, or use Your battles for account results.",
            id=prefix + "-empty",
            classes="site-empty",
            markup=False,
        )
        with ActionBar(classes="actions"):
            yield Button(
                "Inspect / replay", id=prefix + "-inspect", disabled=True, classes="primary"
            )
            yield Button("Previous", id=prefix + "-previous")
            yield Button("Next", id=prefix + "-next")
            yield Button("Refresh", id=prefix + "-refresh")
            yield Button("Official page", id=prefix + "-web")


class Updates(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Official announcements", classes="hint")
        with VerticalScroll(classes="document-scroll"):
            yield Markdown(
                "# Updates\n\nLoading the public announcement page…",
                id="updates-document",
                open_links=False,
            )
        with ActionBar(classes="actions"):
            yield Button("Refresh", id="updates-refresh")
            yield Button("Official page", id="updates-web")


class Ratings(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="site-filter-row"):
            yield Input(placeholder="Search a team or member", id="ratings-search")
            yield Select(
                [("Your team", "ours")], value="ours", allow_blank=False, id="ratings-team"
            )
        yield Static(
            "Select a team to inspect its rating and rank history.",
            id="ratings-summary",
            classes="hint",
            markup=False,
        )
        with Grid(id="ratings-charts", classes="site-charts"):
            yield HistoryChart("Rating", id="ratings-elo")
            yield HistoryChart("Rank", inverse=True, id="ratings-rank")
        yield DataTable(id="ratings-table", cursor_type="row")
        with ActionBar(classes="actions"):
            yield Button("Team profile", id="ratings-profile")
            yield Button("Refresh", id="ratings-refresh")


class Tournaments(Vertical):
    def compose(self) -> ComposeResult:
        yield DataTable(id="tournaments-table", cursor_type="row")
        yield Static(
            "Connect an API key to load the documented tournament feed.",
            id="tournaments-state",
            classes="hint",
            markup=False,
        )
        yield Tree("Select a tournament to inspect its rounds and bracket", id="tournament-bracket")
        with ActionBar(classes="actions"):
            yield Button(
                "Inspect bracket", id="tournament-inspect", disabled=True, classes="primary"
            )
            yield Button("Refresh", id="tournaments-refresh")
            yield Button("Official page", id="tournaments-web")


class Documentation(Vertical):
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search all 23 official guide topics", id="docs-search")
        with Horizontal(id="docs-workspace"):
            yield OptionList(id="docs-topics")
            with VerticalScroll(classes="document-scroll"):
                yield Markdown("# Overview", id="docs-document", open_links=False)
        yield Static(
            "Offline technical notes; refresh for the current official guide.",
            id="docs-state",
            classes="hint",
            markup=False,
        )
        with ActionBar(classes="actions"):
            yield Button("Refresh topic", id="docs-refresh")
            yield Button("Official topic", id="docs-web")

    @staticmethod
    def topics():
        return [(slug, label, group) for group, topics in DOCS for slug, label in topics]


class Visualiser(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Replay visualiser", classes="page-title")
        yield Static(
            "Round-end playback, timeline seeking, tile inspection and full recorded-event diagnostics. Files stay local.",
            classes="hint",
            markup=False,
        )
        with Horizontal(classes="path-row"):
            yield Input(
                placeholder="Local .replay, .replay.gz or supported JSON", id="visualiser-path"
            )
            yield Button("Browse", id="visualiser-browse")
        with ActionBar(classes="actions"):
            yield Button("Open replay", id="visualiser-open", classes="primary")
            yield Button("Saved replays", id="visualiser-library")
            yield Button("Simulation results", id="visualiser-results")
            yield Button("Try sample", id="visualiser-sample")
        yield Static(
            "Open a local file, a saved Simulation result, or a downloaded server game. Keyboard: Space to play/pause, arrows to step, Home/End to seek, Escape to return.",
            classes="site-explainer",
            markup=False,
        )
        yield DataTable(id="visualiser-replays", cursor_type="row")


class TeamProfile(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("Your team", id="profile-name", classes="profile-name", markup=False)
        yield Static("", id="profile-bio", classes="hint", markup=False)
        with Horizontal(id="profile-facts"):
            with Vertical(classes="profile-fact"):
                yield Static("Rating", classes="metric-label")
                yield BoldDigits("--", id="profile-elo")
                yield Static("Peak n/a", id="profile-peak", classes="hint", markup=False)
            with Vertical(classes="profile-fact"):
                yield Static("Rank", classes="metric-label")
                yield BoldDigits("--", id="profile-rank")
                yield Static("Best n/a", id="profile-best", classes="hint", markup=False)
            with Vertical(classes="profile-fact"):
                yield Static("Record", classes="metric-label")
                yield Static("n/a", id="profile-record", markup=False)
                yield Static("", id="profile-winrate", classes="hint", markup=False)
            with Vertical(classes="profile-fact"):
                yield Static("You vs them", classes="metric-label")
                yield Static("n/a", id="profile-versus", markup=False)
                yield Static("Server record when supplied", classes="hint")
        with Grid(id="profile-charts", classes="site-charts"):
            yield HistoryChart("Rating", id="profile-elo-chart")
            yield HistoryChart("Rank", inverse=True, id="profile-rank-chart")
        with Grid(id="profile-panels"):
            with Vertical(classes="site-panel"):
                yield Static("Recent battles", classes="panel-heading")
                yield DataTable(id="profile-battles", cursor_type="row")
            with Vertical(classes="site-panel"):
                yield Static(
                    "Members", id="profile-members-title", classes="panel-heading", markup=False
                )
                yield DataTable(id="profile-members", cursor_type="row")
        with TabbedContent(id="team-detail-tabs"):
            with TabPane("Eligibility", id="team-eligibility"):
                yield Static(
                    "No eligibility information supplied.",
                    id="profile-eligibility",
                    classes="site-explainer",
                    markup=False,
                )
            with TabPane("Join requests", id="team-requests"):
                yield DataTable(id="profile-requests", cursor_type="row")
                yield Static(
                    "Membership changes are supported on the website only.", classes="hint"
                )
            with TabPane("Settings", id="team-settings"):
                yield Static(
                    "Team name, bio, institution, skins, autoscrims, membership and invite settings are changed on the official website. The JSON API is read-only for these operations.",
                    id="profile-settings",
                    classes="site-explainer",
                    markup=False,
                )
        with ActionBar(classes="actions"):
            yield Button("Challenge team", id="profile-challenge", classes="primary", disabled=True)
            yield Button("Your team", id="profile-own")
            yield Button("Star on website", id="profile-star")
            yield Button("Manage on website", id="profile-manage")
            yield Button("API keys", id="profile-keys")


class FindTeams(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="site-filter-row"):
            yield Input(placeholder="Search team, member, institution or ID", id="teams-search")
            yield Select(
                [("All teams", "all"), ("Prize eligible", "eligible"), ("Has members", "members")],
                value="all",
                allow_blank=False,
                id="teams-filter",
            )
        yield Static("", id="teams-state", classes="hint", markup=False)
        yield DataTable(id="teams-table", cursor_type="row")
        with ActionBar(classes="actions"):
            yield Button("Team profile", id="teams-profile", classes="primary", disabled=True)
            yield Button("Challenge", id="teams-challenge", disabled=True)
            yield Button("Refresh", id="teams-refresh")
            yield Button("Join / manage on website", id="teams-web")


class YourBattles(Vertical):
    def compose(self) -> ComposeResult:
        yield Static(
            "Your account's battle series. Select a row to inspect its games, queue position and judge log.",
            classes="hint",
        )
        yield Select(
            [("All modes", "all"), ("Ranked", "ranked"), ("Unranked", "unranked")],
            value="all",
            allow_blank=False,
            id="my-battles-filter",
        )
        yield DataTable(id="my-battles-table", cursor_type="row")
        yield Static(
            "No account battles loaded.", id="my-battles-state", classes="hint", markup=False
        )
        with ActionBar(classes="actions"):
            yield Button("Inspect games", id="my-battles-inspect", classes="primary", disabled=True)
            yield Button("New challenge", id="my-battles-challenge")
            yield Button("Refresh", id="my-battles-refresh")
