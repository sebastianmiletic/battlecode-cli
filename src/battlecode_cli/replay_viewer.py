"""Mouse/keyboard replay playback with round-end frames, not a game simulator."""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, ScrollableContainer
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Select, Static

from .dialogs import TextViewer
from .replays import Frame, Replay, clean_label


class Timeline(Static):
    can_focus = True

    class Seek(Message):
        def __init__(self, index: int):
            super().__init__()
            self.index = index

    def __init__(self, maximum: int):
        super().__init__(id="replay-timeline")
        self.maximum, self.index = maximum, 0
        self.dragging = False

    def render(self) -> Text:
        width = max(1, self.size.width)
        mark = round(self.index / max(1, self.maximum) * (width - 1))
        result = Text("━" * mark, style="#e8e8e8")
        result.append("●", style="bold #e8e8e8")
        result.append("─" * (width - mark - 1), style="#555555")
        return result

    def seek_at(self, event: events.MouseEvent) -> None:
        offset = event.get_content_offset(self)
        if offset is not None:
            self.post_message(
                self.Seek(
                    round(
                        max(0, min(offset.x, self.size.width - 1))
                        / max(1, self.size.width - 1)
                        * self.maximum
                    )
                )
            )

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            self.dragging = True
            self.capture_mouse()
            self.seek_at(event)
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self.dragging:
            self.seek_at(event)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self.dragging:
            self.dragging = False
            self.seek_at(event)
            self.release_mouse()
            event.stop()


class ReplayBoard(Static):
    class Inspect(Message):
        def __init__(self, point: tuple[int, int]):
            super().__init__()
            self.point = point

    def __init__(self, replay: Replay):
        super().__init__(id="replay-board", markup=False)
        self.replay = replay
        self.frame = replay.frames[0]
        self.detailed = True

    def show_frame(self, frame: Frame, detailed: bool) -> None:
        self.frame, self.detailed = frame, detailed
        width, height = self.replay.map.width, self.replay.map.height
        self.styles.width = width * 2 + 1 if detailed else width
        self.styles.height = height * 2 + 1 if detailed else height
        self.update(self.picture())

    def picture(self) -> Text:
        terrain = self.replay.map
        width = terrain.width * 2 + 1 if self.detailed else terrain.width
        height = terrain.height * 2 + 1 if self.detailed else terrain.height
        grid = [[" " for _ in range(width)] for _ in range(height)]

        def position(x, y):
            return (x * 2 + 1, y * 2 + 1) if self.detailed else (x, y)

        for y in range(terrain.height):
            for x in range(terrain.width):
                px, py = position(x, y)
                grid[py][px] = ":" if (x, y) in terrain.fountains else "·"
        if self.detailed:
            for (x, y), kind in terrain.horizontal.items():
                grid[y * 2][x * 2 + 1] = "─" if kind == 1 else "@"
            for (x, y), kind in terrain.vertical.items():
                grid[y * 2 + 1][x * 2] = "│" if kind == 1 else "@"
        for x, y in self.frame.pearls:
            px, py = position(x, y)
            grid[py][px] = "*"
        for dragon in self.frame.dragons.values():
            for x, y in dragon.body:
                px, py = position(x, y)
                grid[py][px] = "ab"[dragon.team]
        for dragon in self.frame.dragons.values():
            x, y = dragon.body[0]
            px, py = position(x, y)
            grid[py][px] = (
                "QR"[dragon.team] if dragon.id == terrain.queens[dragon.team] else "AB"[dragon.team]
            )
        styles = {
            "Q": "bold reverse #e8e8e8",
            "R": "bold reverse #b0b0b0",
            "A": "bold #e8e8e8",
            "B": "bold #b0b0b0",
            "a": "#d0d0d0",
            "b": "#999999",
            "*": "bold #e8e8e8",
            "@": "bold #a0a0a0",
        }
        output = Text()
        # Coalesce equal-style runs instead of allocating a Rich span for every empty tile.
        for line_index, line in enumerate(grid):
            current_style, run = None, []
            for char in line:
                style = styles.get(char, "#666666")
                if current_style is not None and style != current_style:
                    output.append("".join(run), style=current_style)
                    run = []
                current_style = style
                run.append(char)
            output.append("".join(run), style=current_style)
            if line_index < height - 1:
                output.append("\n")
        return output

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None:
            return
        if self.detailed:
            if offset.x % 2 == 0 or offset.y % 2 == 0:
                return
            x, y = offset.x // 2, offset.y // 2
        else:
            x, y = offset.x, offset.y
        if 0 <= x < self.replay.map.width and 0 <= y < self.replay.map.height:
            self.post_message(self.Inspect((x, y)))


class ReplayViewer(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("space", "play", "Play / pause"),
        Binding("left", "step(-1)", "Previous", show=False),
        Binding("right", "step(1)", "Next", show=False),
        Binding("home", "seek(0)", "Start", show=False),
        Binding("end", "last", "End", show=False),
        Binding("1,2,3,4,5,6,7,8,r", "noop", "", show=False),
    ]

    def __init__(self, replay: Replay, name: str = "Replay"):
        super().__init__(classes="replay-screen")
        self.replay, self.replay_name = replay, clean_label(name)
        self.index = 0
        self.playing = False
        self.detailed = True

    def compose(self) -> ComposeResult:
        yield Static(
            f"{self.replay.map.name}  /  {self.replay_name}", id="replay-heading", markup=False
        )
        yield Static("", id="replay-stats", markup=False)
        yield Static(
            "Q/R starting queens · A/B heads · a/b bodies · * pearl · @ portal · lines: kelp edges",
            id="replay-legend",
            markup=False,
        )
        with ScrollableContainer(id="replay-viewport"):
            yield ReplayBoard(self.replay)
        yield Static(
            "Click a dragon to inspect it. Scroll the map to pan.",
            id="replay-inspector",
            markup=False,
        )
        yield Timeline(len(self.replay.frames) - 1)
        yield Static("", id="replay-position", markup=False)
        with HorizontalScroll(id="replay-buttons", classes="actions"):
            yield Button("First", id="replay-first")
            yield Button("Prev", id="replay-prev")
            yield Button("Play", id="replay-play", classes="primary")
            yield Button("Next", id="replay-next")
            yield Button("Last", id="replay-last")
            yield Button("Events", id="replay-events")
            yield Button("Back", id="replay-back")
        with Horizontal(id="replay-options"):
            yield Select(
                [("Detailed edges", "detailed"), ("Compact (no edges)", "compact")],
                value="detailed",
                allow_blank=False,
                id="replay-mode",
            )
            yield Select(
                [("1 frame/s", 1), ("4 frames/s", 4), ("8 frames/s", 8)],
                value=4,
                allow_blank=False,
                id="replay-speed",
            )
            yield Input(placeholder="Frame #", type="integer", id="replay-jump")
            yield Button("Jump", id="replay-go")
        yield Footer()

    def on_mount(self) -> None:
        self.timer = self.set_interval(0.125, self.tick)
        self.last_tick = 0
        self.show_frame()
        self.query_one("#replay-play", Button).focus()
        self.call_after_refresh(self.fit_initial_map)

    def fit_initial_map(self) -> None:
        viewport = self.query_one("#replay-viewport").content_region
        self.detailed = (
            viewport.height >= self.replay.map.height * 2 + 1
            and viewport.width >= self.replay.map.width * 2 + 1
        )
        self.query_one("#replay-mode", Select).value = "detailed" if self.detailed else "compact"
        self.show_frame()

    def action_noop(self) -> None:
        pass

    def on_unmount(self) -> None:
        self.timer.stop()

    def show_frame(self) -> None:
        frame = self.replay.frames[self.index]
        self.query_one(ReplayBoard).show_frame(frame, self.detailed)
        lines = []
        for side, bot, stats in zip(("A", "B"), self.replay.bots, frame.stats, strict=True):
            lines.append(
                f"{side}: {bot[:24]}  ·  queen {stats.queen}  ·  units {stats.living}  ·  longest {stats.longest}  ·  total {stats.total}"
            )
        self.query_one("#replay-stats", Static).update("\n".join(lines))
        phase = "Start" if frame.round < 0 else f"After round {frame.round}"
        self.query_one("#replay-position", Static).update(
            f"Frame {self.index}/{len(self.replay.frames) - 1}  ·  {phase}  ·  {self.replay.end_reason}  ·  {'Winner ' + self.replay.winner if self.replay.winner else 'No winner'}"
        )
        timeline = self.query_one(Timeline)
        timeline.index = self.index
        timeline.refresh()
        self.query_one("#replay-prev", Button).disabled = self.index == 0
        self.query_one("#replay-first", Button).disabled = self.index == 0
        self.query_one("#replay-next", Button).disabled = self.index == len(self.replay.frames) - 1
        self.query_one("#replay-last", Button).disabled = self.index == len(self.replay.frames) - 1
        self.query_one("#replay-legend", Static).update(
            "Q/R queens · A/B heads · a/b bodies · * pearl · @ portal · lines: kelp edges"
            if self.detailed
            else "COMPACT: edges hidden. Q/R queens · A/B heads · a/b bodies · * pearl"
        )

    def action_seek(self, index: int) -> None:
        self.index = max(0, min(index, len(self.replay.frames) - 1))
        self.show_frame()

    def action_last(self) -> None:
        self.action_seek(len(self.replay.frames) - 1)

    def action_step(self, delta: int) -> None:
        self.pause()
        self.action_seek(self.index + delta)

    def pause(self) -> None:
        self.playing = False
        self.query_one("#replay-play", Button).label = "Play"

    def action_play(self) -> None:
        if self.index == len(self.replay.frames) - 1:
            self.action_seek(0)
        self.playing = not self.playing
        self.query_one("#replay-play", Button).label = "Pause" if self.playing else "Play"
        self.last_tick = 0

    def tick(self) -> None:
        if not self.playing:
            return
        self.last_tick += 1
        speed = int(self.query_one("#replay-speed", Select).value)
        if self.last_tick < 8 // speed:
            return
        self.last_tick = 0
        self.action_seek(self.index + 1)
        if self.index == len(self.replay.frames) - 1:
            self.pause()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Timeline.Seek)
    def mouse_seek(self, event: Timeline.Seek) -> None:
        self.pause()
        self.action_seek(event.index)

    @on(ReplayBoard.Inspect)
    def inspect(self, event: ReplayBoard.Inspect) -> None:
        frame = self.replay.frames[self.index]
        dragon = next((d for d in frame.dragons.values() if event.point in d.body), None)
        if dragon:
            role = (
                "starting queen" if dragon.id == self.replay.map.queens[dragon.team] else "dragon"
            )
            text = f"Team {'AB'[dragon.team]} {role} #{dragon.id} · length {len(dragon.body)} · head {dragon.body[0]}"
        else:
            text = f"Tile {event.point} · {'pearl' if event.point in frame.pearls else 'empty'}"
        self.query_one("#replay-inspector", Static).update(text)

    @on(Select.Changed, "#replay-mode")
    def mode_changed(self, event: Select.Changed) -> None:
        self.detailed = event.value == "detailed"
        if self.is_mounted:
            self.show_frame()

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        ident = event.button.id
        event.stop()
        if ident == "replay-first":
            self.pause()
            self.action_seek(0)
        elif ident == "replay-prev":
            self.action_step(-1)
        elif ident == "replay-play":
            self.action_play()
        elif ident == "replay-next":
            self.action_step(1)
        elif ident == "replay-last":
            self.pause()
            self.action_last()
        elif ident == "replay-back":
            self.action_back()
        elif ident == "replay-events":
            self.pause()
            frame = self.replay.frames[self.index]
            self.app.push_screen(
                TextViewer(
                    f"Frame {self.index}: splits and deaths",
                    "\n".join(frame.events)
                    or "No splits or deaths in this frame. Playback shows round-end states; action logs and sonar overlays are not displayed.",
                )
            )
        elif ident == "replay-go":
            value = self.query_one("#replay-jump", Input).value
            if value.isdigit() and 0 <= int(value) < len(self.replay.frames):
                self.pause()
                self.action_seek(int(value))
            else:
                self.app.notify(f"Enter a frame from 0 to {len(self.replay.frames) - 1}.")
