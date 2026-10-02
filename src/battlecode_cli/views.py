"""Six work surfaces. Related workflows share tabs, not duplicate navigation."""

from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Input,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)

from .charts import BoldDigits, HistoryChart


class ActionBar(HorizontalScroll):
    """Focused buttons scroll into view in narrow terminal windows."""


class Overview(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Your team", id="team-summary", markup=False)
        with Horizontal(id="rating-row"):
            with Vertical(id="elo-metric"):
                yield Static("ELO", classes="metric-label")
                yield BoldDigits("--", id="elo-value")
            with Vertical(id="rank-metric"):
                yield Static("RANK", classes="metric-label")
                yield BoldDigits("--", id="rank-value")
            yield Static("", id="team-record", markup=False)
        with Horizontal(id="history-row"):
            yield HistoryChart("ELO history", id="elo-history")
            yield HistoryChart("Rank history", inverse=True, id="rank-history")
        yield Static("No active bot", id="active-summary", markup=False)
        with Vertical(id="recent-panel"):
            yield Static("Recent games", classes="section-title")
            yield DataTable(id="overview-games", cursor_type="row", zebra_stripes=False)
        # Retain the small summary table as a data adapter; versions are now only shown in Bots.
        yield DataTable(id="overview-bots", classes="hidden", cursor_type="row")
        yield Static(
            "Connect an API key (6) for your team statistics.",
            id="overview-empty",
            classes="empty",
            markup=False,
        )


class Bots(Vertical):
    def compose(self) -> ComposeResult:
        with TabbedContent(id="bot-tabs"):
            with TabPane("Versions", id="bot-versions"):
                with Vertical(classes="tab-workspace"):
                    yield DataTable(id="bots-table", cursor_type="row", zebra_stripes=False)
                    yield Static(
                        "No bots yet. Open Upload to add a version.",
                        id="bots-empty",
                        classes="empty",
                        markup=False,
                    )
                    with Vertical(classes="detail-area"):
                        yield Static("Select a bot", id="bot-detail", markup=False)
                        with ActionBar(classes="actions"):
                            yield Button(
                                "Activate", id="activate-bot", classes="primary", disabled=True
                            )
                            yield Button("Details / log", id="bot-log", disabled=True)
                            yield Button("Download", id="download-bot", disabled=True)
                            yield Button("Arena A", id="arena-use-a", disabled=True)
                            yield Button("Arena B", id="arena-use-b", disabled=True)
                            yield Button("Upload", id="bots-upload")
            with TabPane("Upload", id="bot-upload"):
                yield Upload(id="upload")


class Games(Vertical):
    def compose(self) -> ComposeResult:
        with TabbedContent(id="game-tabs"):
            with TabPane("Matches", id="game-matches"):
                with Vertical(classes="tab-workspace"):
                    yield Select(
                        [("All games", "all"), ("Ranked", "ranked"), ("Unranked", "unranked")],
                        value="all",
                        allow_blank=False,
                        id="games-filter",
                    )
                    yield DataTable(id="games-table", cursor_type="row", zebra_stripes=False)
                    yield Static(
                        "No games in this view.", id="games-empty", classes="empty", markup=False
                    )
                    yield Static("Select a battle to see its maps", id="game-detail", markup=False)
                    yield DataTable(id="game-parts", cursor_type="row", zebra_stripes=False)
                    with ActionBar(classes="actions"):
                        yield Button(
                            "View replay", id="open-replay", classes="primary", disabled=True
                        )
                        yield Button("Download", id="download-replay", disabled=True)
                        yield Button("Web", id="view-game", disabled=True)
                        yield Button("Judge log", id="game-log", disabled=True)
            with TabPane("Local replays", id="game-local"):
                yield Replays(id="replays")


class Leaderboard(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Leaderboard", classes="page-title")
        yield Static("", id="leaderboard-state", classes="hint", markup=False)
        yield Input(placeholder="Search teams, members or ID", id="ladder-search")
        yield DataTable(id="ladder-table", cursor_type="row", zebra_stripes=False)
        yield Static(
            "Connect an API key to load live standings.",
            id="ladder-empty",
            classes="empty",
            markup=False,
        )
        with ActionBar(classes="actions"):
            yield Button("Challenge team", id="ladder-challenge", classes="primary", disabled=True)
            yield Button("Refresh", id="leaderboard-refresh")


class Upload(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("Bot ZIP or project folder (max 4 MB)", classes="field-label")
        with Horizontal(id="path-row"):
            yield Input(placeholder="~/Coding/my-bot or ~/Downloads/bot.zip", id="upload-path")
            yield Button("Browse", id="browse-bot")
        yield Static("Version name", classes="field-label")
        yield Input(placeholder="e.g. nitro-v1", id="upload-name", max_length=120)
        yield Static("Description (optional)", classes="field-label")
        yield Input(placeholder="What changed?", id="upload-description", max_length=2000)
        yield Static(
            "A successful build may become active. Confirm before sending.",
            classes="form-note",
            markup=False,
        )
        yield Static("", id="upload-status", classes="form-status", markup=False)
        with ActionBar(classes="actions"):
            yield Button("Review upload", id="review-upload", classes="primary")


class Challenge(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("Play other teams with your active server bot.", classes="hint")
        yield Static("Opponent team ID", classes="field-label")
        yield Input(placeholder="Team ID", id="challenge-team", type="integer")
        yield Select(
            [
                ("Unranked: no rating change", "practice"),
                ("Ranked: five games, affects rating", "ranked"),
            ],
            value="practice",
            allow_blank=False,
            id="challenge-mode",
        )
        yield Static("Unranked maps (empty = server choice)", classes="field-label")
        yield SelectionList[int](id="challenge-maps")
        yield Static(
            "Ranked maps are chosen by the server.",
            id="challenge-note",
            classes="form-note",
            markup=False,
        )
        yield Static("", id="challenge-status", classes="form-status", markup=False)
        with ActionBar(classes="actions"):
            yield Button("Review challenge", id="review-challenge", classes="primary")


class Arena(Vertical):
    def compose(self) -> ComposeResult:
        with TabbedContent(id="arena-tabs"):
            with TabPane("Benchmark", id="arena-local"):
                with Vertical(classes="tab-workspace"):
                    with VerticalScroll(id="arena-setup"):
                        yield Static("", id="arena-runner-state", classes="hint", markup=False)
                        yield Static("Bot A: ZIP or project folder", classes="field-label")
                        with Horizontal(classes="path-row"):
                            yield Input(placeholder="First version", id="arena-bot-a")
                            yield Button("Browse", id="arena-browse-a")
                        yield Static("Bot B: ZIP or project folder", classes="field-label")
                        with Horizontal(classes="path-row"):
                            yield Input(placeholder="Second version", id="arena-bot-b")
                            yield Button("Browse", id="arena-browse-b")
                        yield Static("Maps", classes="field-label")
                        yield Select(
                            [
                                ("Official maps", "official"),
                                ("Custom maps", "custom"),
                                ("All stored maps", "all"),
                            ],
                            value="official",
                            allow_blank=False,
                            id="arena-map-source",
                        )
                        yield SelectionList[str](id="arena-local-maps")
                        with ActionBar(classes="actions"):
                            yield Button("Select all", id="arena-select-all")
                            yield Button("Clear", id="arena-clear-maps")
                            yield Button("Get official maps", id="arena-official-maps")
                        with Collapsible(title="Import custom maps", collapsed=True):
                            with Horizontal(classes="path-row"):
                                yield Input(
                                    placeholder="Folder, ZIP, .map or .txt", id="arena-map-path"
                                )
                                yield Button("Browse", id="arena-browse-maps")
                            with ActionBar(classes="actions"):
                                yield Button(
                                    "Import maps", id="arena-import-maps", classes="primary"
                                )
                            yield Static(
                                "Up to 1,000 maps. Local only.", classes="form-note", markup=False
                            )
                        with Collapsible(title="Opponents and run settings", collapsed=True):
                            yield Static(
                                "Other users' shared bots (optional)", classes="field-label"
                            )
                            with Horizontal(classes="path-row"):
                                yield Input(
                                    placeholder="Folder of opponent ZIPs or projects",
                                    id="arena-opponents",
                                )
                                yield Button("Browse", id="arena-browse-opponents")
                            yield Static(
                                "Both versions play each opponent; A vs B is always included.",
                                classes="form-note",
                                markup=False,
                            )
                            yield Checkbox(
                                "Swap seats for every map / seed", value=True, id="arena-swap"
                            )
                            yield Static("Repeats per map (1–10)", classes="field-label")
                            yield Input("1", id="arena-repeats", type="integer")
                            yield Static("Base seed", classes="field-label")
                            yield Input("0", id="arena-seed", type="integer")
                            yield Static("Timeout per game (seconds)", classes="field-label")
                            yield Input("600", id="arena-timeout", type="integer")
                    yield Static("", id="arena-progress", markup=False)
                    with ActionBar(classes="actions"):
                        yield Button("Review benchmark", id="arena-run", classes="primary")
                        yield Button("Stop", id="arena-stop", disabled=True)
                        yield Button("Install runner", id="arena-install")
            with TabPane("Online", id="arena-online"):
                yield Challenge(id="challenge")
            with TabPane("Results", id="arena-results"):
                with Vertical(classes="tab-workspace"):
                    yield DataTable(id="arena-runs-table", cursor_type="row", zebra_stripes=False)
                    yield Static("No benchmarks yet", id="arena-summary", markup=False)
                    yield DataTable(
                        id="arena-results-table", cursor_type="row", zebra_stripes=False
                    )
                    with ActionBar(classes="actions"):
                        yield Button("Full report", id="arena-report", disabled=True)
                        yield Button("View replay", id="arena-replay", disabled=True)
                        yield Button("Export JSON / CSV", id="arena-export", disabled=True)
                        yield Button("Stop", id="arena-stop-results", disabled=True)


class Settings(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("API keys & accounts", id="settings-title", classes="page-title", markup=False)
        yield Static("", id="connection-summary", classes="form-note", markup=False)
        yield Static("Saved API keys", id="saved-keys-title", classes="section-title")
        yield DataTable(id="accounts-table", cursor_type="row", zebra_stripes=False)
        yield Static(
            "No saved keys. Add your first Battlecode API key below.",
            id="accounts-empty",
            classes="form-note",
            markup=False,
        )
        with ActionBar(classes="actions"):
            yield Button("Use selected", id="use-account", classes="primary", disabled=True)
            yield Button("Disconnect", id="disconnect-account", disabled=True)
            yield Button("Delete key", id="delete-account", disabled=True)
            yield Button("Open team page", id="team-page")
            yield Button("Continue offline", id="continue-offline")
        yield Static("New API key", classes="field-label")
        yield Input(
            placeholder="Paste bc_… (hidden). Enter to connect.", id="api-key", password=True
        )
        yield Static("Account label (optional)", classes="field-label")
        yield Input(
            placeholder="Defaults to your verified team name", id="account-label", max_length=64
        )
        with ActionBar(classes="actions"):
            yield Button("Connect & save", id="connect-key", classes="primary")
            yield Button("Save only", id="save-key")
            yield Button("Import existing key", id="import-key")
        yield Static(
            "Only one key is connected. Local deletion does not revoke the server key.",
            classes="form-note",
            markup=False,
        )
        yield Static("", id="settings-status", classes="form-status", markup=False)
        yield Static("Local files", classes="section-title")
        yield Static("", id="local-paths", classes="form-note", markup=False)
        yield Static("Key storage", classes="section-title")
        yield Static(
            "OS keyring preferred. Linux/macOS fallback: owner-only, unencrypted files. Windows requires Credential Manager. Disconnect never activates another saved key.",
            classes="form-note",
            markup=False,
        )


class Replays(Vertical):
    def compose(self) -> ComposeResult:
        yield DataTable(id="replays-table", cursor_type="row", zebra_stripes=False)
        yield Static(
            "Import a local replay or try the sample. No API key needed.",
            id="replays-empty",
            classes="empty",
            markup=False,
        )
        with Horizontal(id="replay-path-row"):
            yield Input(placeholder="Path to a .replay or .replay.gz file", id="replay-path")
            yield Button("Browse", id="browse-replay")
        yield Static("", id="replays-status", classes="form-status", markup=False)
        with ActionBar(classes="actions"):
            yield Button("Import", id="import-replay", classes="primary")
            yield Button("Watch", id="watch-replay", disabled=True)
            yield Button("Remove", id="remove-replay", disabled=True)
            yield Button("Try sample", id="sample-replay")
