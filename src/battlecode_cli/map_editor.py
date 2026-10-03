"""Local official-format map editor: text, brushes, validation, undo and confirmed saves."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.geometry import Region
from textual.message import Message
from textual.widgets import Button, Input, Select, Static, TabbedContent, TabPane, TextArea

from .config import atomic_write
from .dialogs import Confirm, FilePicker
from .replay_viewer import ReplayBoard, ReplayViewer
from .replays import Frame, Replay, TeamStats, clean_label, parse_map, path_value
from .views import ActionBar


def new_map(width=16, height=16, name="Untitled map", symmetry="xy") -> str:
    if not 4 <= width <= 128 or not 4 <= height <= 128:
        raise ValueError("New maps need dimensions between 4 and 128.")
    if symmetry not in ("x", "y", "xy", "none"):
        raise ValueError("Choose a valid symmetry.")
    return (
        f"MAP {width} {height}\nMAP_NAME {clean_label(name, 120)}\n"
        + (f"SYMMETRY {symmetry}\n" if symmetry != "none" else "")
        + f"TILE_COUNT 0\nEDGE_COUNT 0\nDRAGON_COUNT 2\nDRAGON 0 2 1 1 0 1\nDRAGON 1 2 {width - 2} {height - 2} {width - 1} {height - 2}\n"
    )


def normalized_counts(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not line.split() or line.split()[0] not in ("TILE_COUNT", "EDGE_COUNT", "DRAGON_COUNT")
    ]
    tags = ("TILE", "EDGE", "DRAGON")
    has_end = any(line.split() == ["END"] for line in lines)
    result = [line for line in lines if not line.split() or line.split()[0] not in (*tags, "END")]
    for tag in tags:
        records = [line for line in lines if line.split() and line.split()[0] == tag]
        result.extend([f"{tag}_COUNT {len(records)}", *records])
    if has_end:
        result.append("END")
    return "\n".join(result).rstrip() + "\n"


def validate_map(text: str):
    if len(text.encode()) > 1024 * 1024:
        raise ValueError("Map source exceeds the 1 MB limit.")
    directives = [
        line.split()
        for line in text.splitlines()
        if line.split() and not line.lstrip().startswith("#")
    ]
    if not directives or directives[0][0] != "MAP":
        raise ValueError("MAP width height must be the first directive.")
    allowed = {
        "MAP",
        "MAP_NAME",
        "SYMMETRY",
        "TILE",
        "EDGE",
        "DRAGON",
        "TILE_COUNT",
        "EDGE_COUNT",
        "DRAGON_COUNT",
        "END",
    }
    if any(parts[0] not in allowed for parts in directives):
        raise ValueError(
            "Unknown map directive; use the official format in Documentation → Map Files."
        )
    if any(parts[0] == "END" for parts in directives) and (
        directives[-1] != ["END"] or sum(parts[0] == "END" for parts in directives) != 1
    ):
        raise ValueError("END must be a single final directive.")
    board = parse_map(text)
    if None in board.queens:
        raise ValueError("Add a starting queen for each team.")
    portals, occupied, counts, declared = {}, set(), {"TILE": 0, "EDGE": 0, "DRAGON": 0}, {}
    last_team = None
    tiles, edges = set(), set()
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        kind = parts[0]
        if kind in counts:
            counts[kind] += 1
        if kind.endswith("_COUNT") and kind[:-6] in counts:
            if len(parts) != 2:
                raise ValueError("Invalid count directive.")
            declared[kind[:-6]] = int(parts[1])
        elif kind == "SYMMETRY" and (len(parts) != 2 or parts[1] not in ("x", "y", "xy")):
            raise ValueError("SYMMETRY must be x, y or xy.")
        elif kind == "TILE":
            location = tuple(map(int, parts[1:3]))
            if location in tiles:
                raise ValueError("Duplicate TILE location.")
            tiles.add(location)
            minimum, maximum = map(int, parts[3:5])
            if minimum < 0 or maximum < 0 or (maximum and not 1 <= minimum <= maximum):
                raise ValueError("Spawn gaps must be 1 <= min <= max, or max=0 for no spawning.")
        elif kind == "EDGE":
            if len(parts) != 4:
                raise ValueError("EDGE needs index, kind and portalId.")
            index, edge_kind, portal = map(int, parts[1:])
            if index in edges:
                raise ValueError("Duplicate EDGE index.")
            edges.add(index)
            if edge_kind == 1 and portal != -1:
                raise ValueError("Kelp edges use portal id -1.")
            if edge_kind == 2:
                if portal < 0:
                    raise ValueError("Portal edges need a non-negative portal ID.")
                portals.setdefault(portal, []).append((index // (board.width + 1)) % 2)
        elif kind == "DRAGON":
            team, count = int(parts[1]), int(parts[2])
            if team == last_team:
                raise ValueError("Starting dragons must alternate teams.")
            last_team = team
            points = list(zip(map(int, parts[3::2]), map(int, parts[4::2]), strict=True))
            if count < 2 or count != len(points):
                raise ValueError(
                    "Starting dragons need at least two segments and the correct count."
                )
            for index, point in enumerate(points):
                if point in occupied:
                    raise ValueError("Starting dragon segments cannot overlap.")
                occupied.add(point)
                if (
                    index
                    and abs(point[0] - points[index - 1][0]) + abs(point[1] - points[index - 1][1])
                    != 1
                ):
                    raise ValueError("Starting dragon segments must be adjacent.")
    for kind, count in counts.items():
        if declared.get(kind) != count:
            raise ValueError(f"{kind}_COUNT must be {count}. Use Normalize counts.")
    for ident, edges in portals.items():
        if len(edges) != 2 or edges[0] != edges[1]:
            raise ValueError(f"Portal {ident} needs exactly two edges with the same orientation.")
    return board


def preview_replay(text: str) -> Replay:
    board = parse_map(text)
    spawns = frozenset(
        (int(parts[1]), int(parts[2]))
        for line in text.splitlines()
        if (parts := line.split()) and parts[0] == "TILE" and int(parts[4]) > 0
    )
    board = replace(board, fountains=spawns)
    dragons = {dragon.id: dragon for dragon in board.initial}
    stats = []
    for team in (0, 1):
        own = [dragon for dragon in board.initial if dragon.team == team]
        queen = dragons.get(board.queens[team])
        stats.append(
            TeamStats(
                len(own),
                max((len(d.body) for d in own), default=0),
                sum(len(d.body) for d in own),
                len(queen.body) if queen else 0,
                0,
                0,
            )
        )
    frame = Frame(-1, dragons, frozenset(), tuple(stats), ())
    return Replay(board, ("Starting team A", "Starting team B"), (frame,), None, "Map preview")


class EditorBoard(ReplayBoard):
    can_focus = True

    class Paint(Message):
        def __init__(self, point):
            super().__init__()
            self.point = point

    def __init__(self, replay):
        super().__init__(replay, ident="editor-board")
        self.cursor = (0, 0)

    def picture(self):
        output = super().picture()
        x, y = self.cursor
        width = self.replay.map.width * 2 + 1 if self.detailed else self.replay.map.width
        px, py = (2 * x + 1, 2 * y + 1) if self.detailed else (x, y)
        index = py * (width + 1) + px
        output.stylize("bold reverse", index, index + 1)
        return output

    def show_cursor(self):
        self.update(self.picture())
        x, y = self.cursor
        px, py = (2 * x + 1, 2 * y + 1) if self.detailed else (x, y)
        self.parent.scroll_to_region(Region(px, py, 1, 1), animate=False)

    def on_click(self, event: events.Click):
        offset = event.get_content_offset(self)
        if offset is None:
            return
        x, y = (offset.x // 2, offset.y // 2) if self.detailed else (offset.x, offset.y)
        if 0 <= x < self.replay.map.width and 0 <= y < self.replay.map.height:
            self.cursor = (x, y)
            self.show_cursor()
            self.post_message(self.Paint(self.cursor))
            self.focus()

    def on_key(self, event: events.Key):
        if event.key in ("left", "right", "up", "down"):
            dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[event.key]
            x, y = self.cursor
            self.cursor = (
                max(0, min(self.replay.map.width - 1, x + dx)),
                max(0, min(self.replay.map.height - 1, y + dy)),
            )
            self.show_cursor()
            self.post_message(ReplayBoard.Inspect(self.cursor))
            event.stop()
        elif event.key in ("enter", "space"):
            self.post_message(self.Paint(self.cursor))
            event.stop()


class MapEditor(Vertical):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.undo, self.redo = [], []
        self.previous = new_map()
        self.syncing = False
        self.dirty = False
        self.busy = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="site-filter-row"):
            yield Input("Untitled map", id="editor-name", tooltip="Map name", max_length=120)
            yield Input("16", type="integer", id="editor-width", tooltip="Width in tiles")
            yield Input("16", type="integer", id="editor-height", tooltip="Height in tiles")
            yield Select(
                [
                    ("Symmetry xy", "xy"),
                    ("Symmetry x", "x"),
                    ("Symmetry y", "y"),
                    ("No symmetry", "none"),
                ],
                value="xy",
                allow_blank=False,
                id="editor-symmetry",
            )
            yield Button("New", id="editor-new")
        with Horizontal(classes="site-filter-row"):
            yield Select(
                [
                    ("Inspect", "inspect"),
                    ("Pearl spawn", "spawn"),
                    ("Kelp edge", "kelp"),
                    ("Portal edge", "portal"),
                    ("Queen A", "queen-a"),
                    ("Queen B", "queen-b"),
                    ("Erase spawn", "erase-spawn"),
                    ("Erase edge", "erase-edge"),
                ],
                value="inspect",
                allow_blank=False,
                id="editor-tool",
            )
            yield Select(
                [("North", "north"), ("East", "east"), ("South", "south"), ("West", "west")],
                value="north",
                allow_blank=False,
                id="editor-direction",
            )
            yield Input("8", type="integer", id="editor-min-gap", tooltip="Minimum pearl spawn gap")
            yield Input(
                "20", type="integer", id="editor-max-gap", tooltip="Maximum pearl spawn gap"
            )
            yield Input("0", type="integer", id="editor-portal-id", tooltip="Portal pair ID")
        yield Static(
            "Spawn min / max gaps; portal ID. Click a tile, or use arrows and Enter on the board. Text editing preserves all directives.",
            classes="hint",
            markup=False,
        )
        with TabbedContent(id="editor-tabs"):
            with TabPane("Map", id="editor-map-tab"):
                with ScrollableContainer(id="editor-viewport"):
                    yield EditorBoard(preview_replay(self.previous))
            with TabPane("Source", id="editor-source-tab"):
                yield TextArea(
                    self.previous, id="editor-source", show_line_numbers=True, soft_wrap=False
                )
        yield Static(
            "Local map only; not uploaded.", id="editor-status", classes="hint", markup=False
        )
        with Horizontal(classes="path-row"):
            yield Input(placeholder="Local .map path to load or save", id="editor-path")
            yield Button("Browse", id="editor-browse")
            yield Button("Load", id="editor-load")
        with ActionBar(classes="actions"):
            yield Button("Validate", id="editor-validate", classes="primary")
            yield Button("Normalize counts", id="editor-counts")
            yield Button("Undo", id="editor-undo")
            yield Button("Redo", id="editor-redo")
            yield Button("Preview", id="editor-preview")
            yield Button("Save map", id="editor-save")
            yield Button("Add to Arena", id="editor-arena")

    def on_mount(self):
        self.refresh_board()

    @property
    def text(self):
        return self.query_one("#editor-source", TextArea).text

    def set_text(self, text: str, *, history=True):
        if len(text.encode()) > 1024 * 1024:
            raise ValueError("Map source exceeds the 1 MB limit.")
        if history and text != self.previous:
            self.undo.append(self.previous)
            self.undo = self.undo[-50:]
            self.redo.clear()
        self.previous = text
        self.syncing = True
        self.query_one("#editor-source", TextArea).load_text(text)
        self.syncing = False
        self.dirty = True
        self.refresh_board()

    @on(TextArea.Changed, "#editor-source")
    def source_changed(self):
        if self.syncing or self.text == self.previous:
            return
        if len(self.text.encode()) > 1024 * 1024:
            self.set_text(self.previous, history=False)
            self.query_one("#editor-status", Static).update(
                "Map source exceeds the 1 MB limit; last draft restored."
            )
            return
        self.undo.append(self.previous)
        self.undo = self.undo[-50:]
        self.previous = self.text
        self.redo.clear()
        self.dirty = True
        self.refresh_board()

    def refresh_board(self):
        try:
            replay = preview_replay(self.text)
            board = self.query_one("#editor-board", EditorBoard)
            board.replay = replay
            x, y = board.cursor
            board.cursor = (min(x, replay.map.width - 1), min(y, replay.map.height - 1))
            board.show_frame(replay.frames[0], True)
            try:
                validate_map(self.text)
                message = (
                    f"Valid format · {replay.map.width}×{replay.map.height} · {len(replay.map.initial)} starting dragons · unsaved changes"
                    if self.dirty
                    else "Map loaded · local only"
                )
            except ValueError as error:
                message = str(error)
            self.query_one("#editor-status", Static).update(message)
        except (ValueError, OSError) as error:
            self.query_one("#editor-status", Static).update(str(error))

    @on(EditorBoard.Paint)
    def paint(self, message):
        x, y = message.point
        tool = self.query_one("#editor-tool", Select).value
        try:
            board = parse_map(self.text)
            if tool == "inspect":
                self.inspect_point((x, y))
                return
            lines = self.text.splitlines()
            if tool in ("spawn", "erase-spawn"):
                lines = [line for line in lines if line.split()[:3] != ["TILE", str(x), str(y)]]
                if tool == "spawn":
                    minimum = int(self.query_one("#editor-min-gap", Input).value)
                    maximum = int(self.query_one("#editor-max-gap", Input).value)
                    if not 1 <= minimum <= maximum <= 10000:
                        raise ValueError("Use spawn gaps from 1 to 10,000, min <= max.")
                    lines.append(f"TILE {x} {y} {minimum} {maximum}")
            elif tool in ("kelp", "portal", "erase-edge"):
                direction = self.query_one("#editor-direction", Select).value
                ex, ey = x + (direction == "east"), y + (direction == "south")
                row = 2 * ey if direction in ("north", "south") else 2 * ey + 1
                index = row * (board.width + 1) + ex
                lines = [line for line in lines if line.split()[:2] != ["EDGE", str(index)]]
                if tool != "erase-edge":
                    portal = (
                        int(self.query_one("#editor-portal-id", Input).value)
                        if tool == "portal"
                        else -1
                    )
                    if tool == "portal" and not 0 <= portal <= 65535:
                        raise ValueError("Portal IDs range from 0 to 65,535.")
                    lines.append(f"EDGE {index} {2 if tool == 'portal' else 1} {portal}")
            elif tool in ("queen-a", "queen-b"):
                team = 0 if tool == "queen-a" else 1
                direction = -1 if team == 0 else 1
                tail = x + direction
                if not 0 <= tail < board.width:
                    raise ValueError("Place the head one tile away from this horizontal boundary.")
                changed = False
                for index, line in enumerate(lines):
                    parts = line.split()
                    if not changed and parts[:2] == ["DRAGON", str(team)]:
                        lines[index] = f"DRAGON {team} 2 {x} {y} {tail} {y}"
                        changed = True
                if not changed:
                    raise ValueError("Create a new map with two starting queens first.")
            self.set_text(normalized_counts("\n".join(lines)))
        except ValueError as error:
            self.query_one("#editor-status", Static).update(str(error))

    @on(ReplayBoard.Inspect)
    def board_inspected(self, message):
        self.inspect_point(message.point)

    def inspect_point(self, point):
        x, y = point
        try:
            board = parse_map(self.text)
        except ValueError as error:
            self.query_one("#editor-status", Static).update(str(error))
            return
        dragon = next((dragon for dragon in board.initial if point in dragon.body), None)
        self.query_one("#editor-status", Static).update(
            f"Tile ({x}, {y}) · "
            + (
                f"team {'AB'[dragon.team]}, dragon {dragon.id}, length {len(dragon.body)}"
                if dragon
                else "empty / spawn terrain"
            )
            + " · Enter applies the selected brush"
        )

    @on(Button.Pressed)
    def pressed(self, event):
        ident = event.button.id
        if not ident or not ident.startswith("editor-"):
            return
        event.stop()
        if self.busy:
            return
        try:
            if ident == "editor-new":
                self.new_review()
            elif ident == "editor-counts":
                self.set_text(normalized_counts(self.text))
            elif ident == "editor-validate":
                board = validate_map(self.text)
                self.query_one("#editor-status", Static).update(
                    f"Valid official format: {board.width}×{board.height}. Engine validation remains authoritative."
                )
            elif ident == "editor-undo" and self.undo:
                self.redo.append(self.previous)
                self.set_text(self.undo.pop(), history=False)
            elif ident == "editor-redo" and self.redo:
                self.undo.append(self.previous)
                self.set_text(self.redo.pop(), history=False)
            elif ident == "editor-browse":
                self.app.push_screen(
                    FilePicker(self.query_one("#editor-path", Input).value, kind="maps"),
                    self.picked_path,
                )
            elif ident == "editor-load":
                self.load_map()
            elif ident == "editor-save":
                self.save_map()
            elif ident == "editor-preview":
                validate_map(self.text)
                self.app.push_screen(ReplayViewer(preview_replay(self.text), "Local map preview"))
            elif ident == "editor-arena":
                self.add_to_arena()
        except (ValueError, OSError) as error:
            self.query_one("#editor-status", Static).update(str(error))

    def picked_path(self, value):
        if value:
            self.query_one("#editor-path", Input).value = value

    def begin_operation(self):
        if self.busy:
            return False
        self.busy = True
        self.return_focus = self.app.focused
        for ident in ("editor-new", "editor-load", "editor-save", "editor-arena"):
            self.query_one("#" + ident, Button).disabled = True
        return True

    def end_operation(self):
        self.busy = False
        if self.is_mounted:
            for ident in ("editor-new", "editor-load", "editor-save", "editor-arena"):
                if button := self.query_one_optional("#" + ident, Button):
                    button.disabled = False
            from textual.widgets import ContentSwitcher

            pages = self.app.query_one_optional("#pages", ContentSwitcher)
            if pages and pages.current == "map-editor" and len(self.app.screen_stack) == 1:
                if self.return_focus and self.return_focus.is_mounted:
                    self.return_focus.focus()

    @work(group="map-editor")
    async def new_review(self):
        if not self.begin_operation():
            return
        try:
            text = new_map(
                int(self.query_one("#editor-width", Input).value),
                int(self.query_one("#editor-height", Input).value),
                self.query_one("#editor-name", Input).value.strip().replace("\n", " "),
                self.query_one("#editor-symmetry", Select).value,
            )
            if self.dirty and not await self.app.push_screen_wait(
                Confirm(
                    "Replace map draft?",
                    "Current draft remains available in Undo. No files are deleted.",
                    "Create map",
                )
            ):
                return
            self.set_text(text)
        except ValueError as error:
            self.query_one("#editor-status", Static).update(str(error))
        finally:
            self.end_operation()

    @work(group="map-editor")
    async def load_map(self):
        if not self.begin_operation():
            return
        try:
            path = path_value(self.query_one("#editor-path", Input).value)
            if not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise ValueError("Choose a local .map file no larger than 1 MB.")
            text = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
            parse_map(text)
            if self.dirty and not await self.app.push_screen_wait(
                Confirm(
                    "Load this map?",
                    "Current draft is retained in Undo. Original file is not changed.",
                    "Load map",
                )
            ):
                return
            self.set_text(text)
            self.dirty = False
            self.refresh_board()
        except (ValueError, OSError) as error:
            self.query_one("#editor-status", Static).update(str(error))
        finally:
            self.end_operation()

    @work(group="map-editor")
    async def save_map(self):
        if not self.begin_operation():
            return
        try:
            text = self.text
            validate_map(text)
            value = self.query_one("#editor-path", Input).value.strip()
            if not value:
                raise ValueError("Enter a destination .map file path.")
            destination = path_value(value)
            if destination.suffix.lower() != ".map":
                raise ValueError("Save to a .map file.")

            def signature():
                if not destination.exists():
                    return None
                info = destination.stat()
                return info.st_ino, info.st_size, info.st_mtime_ns

            reviewed = signature()
            if not await self.app.push_screen_wait(
                Confirm(
                    "Save local map?",
                    f"{destination}\n{'Replace existing file.' if destination.exists() else 'Create a new file.'}\n\nNothing is uploaded or run.",
                    "Save map",
                )
            ):
                return
            if signature() != reviewed:
                raise ValueError(
                    "Destination changed during review. Review saving again; nothing was overwritten."
                )
            await asyncio.to_thread(atomic_write, destination, text.encode())
            self.dirty = self.text != text
            self.query_one("#editor-status", Static).update(
                f"Saved {destination.name}. Local only."
            )
        except (ValueError, OSError) as error:
            self.query_one("#editor-status", Static).update(str(error))
        finally:
            self.end_operation()

    @work(group="map-editor")
    async def add_to_arena(self):
        if not self.begin_operation():
            return
        try:
            text = self.text
            board = validate_map(text)
            if not await self.app.push_screen_wait(
                Confirm(
                    "Add map to Arena?",
                    f"{board.name} · {board.width}×{board.height}\n\nCopies this validated map into the local custom library. It does not start a simulation or upload anything.",
                    "Add map",
                )
            ):
                return
            await asyncio.to_thread(self.app.arena_maps.import_blobs, [(board.name, text.encode())])
            self.app.action_view("arena")
            self.app.query_one("#arena-map-source", Select).value = "custom"
            self.app.render_arena_maps(select_all=True)
            self.app.notify("Map added to the local Arena library.")
        except (ValueError, OSError) as error:
            self.query_one("#editor-status", Static).update(str(error))
        finally:
            self.end_operation()
