"""Views keep forms inline; only irreversible actions get confirmation dialogs."""

from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Select, SelectionList, Static


class Overview(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("CONTROL ROOM", classes="eyebrow")
        yield Static("Connecting your team…", id="team-summary", markup=False)
        yield Static("Active bot  ·  waiting for submissions", id="active-summary", markup=False)
        with Horizontal(id="overview-grid"):
            with Vertical(id="recent-panel"):
                yield Static("LATEST BATTLES", classes="section-title")
                yield DataTable(id="overview-games", cursor_type="row", zebra_stripes=False)
                yield Static("Enter a battle to inspect its games and replays.", classes="hint")
            with Vertical(id="versions-panel"):
                yield Static("BOT VERSIONS", classes="section-title")
                yield DataTable(id="overview-bots", cursor_type="row", zebra_stripes=False)
                yield Static("Win rate = wins / all completed games.", classes="hint")
        yield Static(
            "No account connected. Open Settings (7), or try battlecode --demo.",
            id="overview-empty",
            classes="empty",
            markup=False,
        )
        with ActionBar(classes="actions"):
            yield Button("Upload bot", id="home-upload", classes="primary")
            yield Button("Challenge a team", id="home-challenge")


class ActionBar(HorizontalScroll):
    """Focused buttons scroll into view in narrow terminal windows."""


class Bots(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("SUBMISSIONS", classes="eyebrow")
        yield Static("Your bots", classes="page-title")
        yield Static(
            "Select a version. Enter to load build details and per-map records.", classes="hint"
        )
        yield DataTable(id="bots-table", cursor_type="row", zebra_stripes=False)
        yield Static(
            "No submissions yet. Upload a ZIP or project folder to get started.",
            id="bots-empty",
            classes="empty",
            markup=False,
        )
        with Vertical(classes="detail-area"):
            yield Static("Choose a submission above.", id="bot-detail", markup=False)
            with ActionBar(classes="actions"):
                yield Button("Activate", id="activate-bot", classes="primary", disabled=True)
                yield Button("Build log / map records", id="bot-log", disabled=True)
                yield Button("Download ZIP", id="download-bot", disabled=True)
                yield Button("Upload new", id="bots-upload")


class Games(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("MATCH HISTORY", classes="eyebrow")
        yield Static("Games & replays", classes="page-title")
        yield Static(
            "Enter a battle to load its games. Select a game to download its replay.",
            classes="hint",
        )
        yield DataTable(id="games-table", cursor_type="row", zebra_stripes=False)
        yield Static(
            "No recent battles. Challenge a team to start a practice game.",
            id="games-empty",
            classes="empty",
            markup=False,
        )
        yield Static("Choose a battle above.", id="game-detail", markup=False)
        yield DataTable(id="game-parts", cursor_type="row", zebra_stripes=False)
        with ActionBar(classes="actions"):
            yield Button("Download replay", id="download-replay", classes="primary", disabled=True)
            yield Button("Open replay", id="open-replay", disabled=True)
            yield Button("View on web", id="view-game", disabled=True)
            yield Button("Judge log", id="game-log", disabled=True)


class Ladder(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("OPPONENTS", classes="eyebrow")
        yield Static("The ladder", classes="page-title")
        yield Input(placeholder="Search teams by name or ID", id="ladder-search")
        yield DataTable(id="ladder-table", cursor_type="row", zebra_stripes=False)
        yield Static("Loading the ladder…", id="ladder-empty", classes="empty", markup=False)
        yield Static(
            "Enter a team to prepare a challenge. Practice is the default.", classes="hint"
        )
        yield Button(
            "Challenge selected team", id="ladder-challenge", classes="primary", disabled=True
        )


class Upload(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("NEW VERSION", classes="eyebrow")
        yield Static("Upload a bot", classes="page-title")
        yield Static(
            "ZIP with bot.toml at the root, or a project folder. Max 4 MB.", classes="hint"
        )
        yield Static("Bot path", classes="field-label")
        with Horizontal(id="path-row"):
            yield Input(placeholder="~/Coding/my-bot or ~/Downloads/bot.zip", id="upload-path")
            yield Button("Browse", id="browse-bot")
        yield Static("Version name", classes="field-label")
        yield Input(placeholder="e.g. nitro-v1", id="upload-name", max_length=120)
        yield Static("Description (optional)", classes="field-label")
        yield Input(
            placeholder="What changed in this version?", id="upload-description", max_length=2000
        )
        yield Static(
            "A successful build may become your active bot. Nothing is sent until you confirm.",
            classes="form-note",
            markup=False,
        )
        yield Static("", id="upload-status", classes="form-status", markup=False)
        with ActionBar(classes="actions"):
            yield Button("Review upload", id="review-upload", classes="primary")


class Challenge(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("NEW BATTLE", classes="eyebrow")
        yield Static("Challenge a team", classes="page-title")
        yield Static(
            "Uses your current active bot. Choose an opponent from the ladder or enter its ID.",
            classes="hint",
        )
        yield Static("Opponent team ID", classes="field-label")
        yield Input(placeholder="Team ID", id="challenge-team", type="integer")
        yield Static("Mode", classes="field-label")
        yield Select(
            [
                ("Practice: no rating change", "practice"),
                ("Ranked: five games, affects rating", "ranked"),
            ],
            value="practice",
            allow_blank=False,
            id="challenge-mode",
        )
        yield Static(
            "Practice maps (optional, leave empty for server choice)", classes="field-label"
        )
        yield SelectionList[int](id="challenge-maps")
        yield Static(
            "Practice is the default. Ranked maps are chosen by the server.",
            id="challenge-note",
            classes="form-note",
            markup=False,
        )
        yield Static("", id="challenge-status", classes="form-status", markup=False)
        with ActionBar(classes="actions"):
            yield Button("Review challenge", id="review-challenge", classes="primary")


class Settings(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("ACCOUNT", classes="eyebrow")
        yield Static("Connection & settings", classes="page-title")
        yield Static(
            "Checking saved credentials…",
            id="connection-summary",
            classes="form-note",
            markup=False,
        )
        yield Static("New API key", classes="field-label")
        yield Input(placeholder="bc_… (hidden, never logged)", id="api-key", password=True)
        with ActionBar(classes="actions"):
            yield Button("Connect & save", id="connect-key", classes="primary")
            yield Button("Open team page", id="team-page")
            yield Button("Forget app key", id="forget-key")
        yield Static(
            "Make a key on your team page. Creating a new key invalidates the old one.",
            classes="hint",
        )
        yield Static("", id="settings-status", classes="form-status", markup=False)
        yield Static("LOCAL FILES", classes="section-title")
        yield Static("", id="local-paths", classes="form-note", markup=False)
        yield Static("SAFE BY DEFAULT", classes="section-title")
        yield Static(
            "Every upload, challenge and bot switch requires confirmation.\nPractice is the default. Writes are never retried automatically.\nKeys stay outside this repository. Signed downloads never receive your key.",
            classes="form-note",
            markup=False,
        )
