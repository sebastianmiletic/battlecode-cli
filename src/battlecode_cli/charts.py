"""Small dependency-free terminal line plots. Empty data is never fabricated."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Digits


class BoldDigits(Digits):
    """A heavier three-line numeral face, still selectable as its real value."""

    FACE = {
        "0": ("█▀█", "█ █", "█▄█"),
        "1": (" ▄█", "  █", "  █"),
        "2": ("▀▀█", "▄▀▀", "█▄▄"),
        "3": ("▀▀█", " ▀█", "▄▄█"),
        "4": ("█ █", "▀▀█", "  █"),
        "5": ("█▀▀", "▀▀█", "▄▄█"),
        "6": ("█▀▀", "█▀█", "█▄█"),
        "7": ("▀▀█", "  █", "  █"),
        "8": ("█▀█", "█▀█", "█▄█"),
        "9": ("█▀█", "▀▀█", "▄▄█"),
        "-": ("   ", "▀▀▀", "   "),
        ".": (" ", " ", "▄"),
    }

    def render(self) -> Text:
        return Text(
            "\n".join(
                " ".join(self.FACE.get(character, ("   ",) * 3)[row] for character in self.value)
                for row in range(3)
            ),
            style="bold",
        )


class HistoryChart(Widget):
    DEFAULT_CSS = "HistoryChart { height: 7; width: 1fr; }"

    def __init__(self, title: str, *, inverse: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.inverse = inverse
        self.points: list[tuple[str, float]] = []
        self.inferred = False

    def set_series(self, points: list, *, inferred: bool = False) -> None:
        self.points = points
        self.inferred = inferred
        self.refresh()

    def on_resize(self) -> None:
        self.refresh()

    def render(self) -> Text:
        light = self.app.theme == "battlecode-site-light"
        muted = "#546171" if light else "#88909c"
        plot = "#41786d" if light else "#9ed8cb"
        heading = self.title + (" (inferred)" if self.inferred else "")
        text = Text(heading + "\n", style="bold")
        if len(self.points) < 2:
            text.append("History appears after two observations.", style=muted)
            return text
        width = max(4, self.content_size.width - 7)
        rows = max(1, min(4, self.content_size.height - 3))
        px, py = width * 2, rows * 4
        values = [p[1] for p in self.points]
        dates = [datetime.fromisoformat(point[0]) for point in self.points]
        duration = (dates[-1] - dates[0]).total_seconds()
        low, high = min(values), max(values)
        span = high - low or 1
        grid = [[0] * width for _ in range(rows)]
        bits = ((1, 8), (2, 16), (4, 32), (64, 128))

        def position(index):
            fraction = (
                (dates[index] - dates[0]).total_seconds() / duration
                if duration
                else index / (len(values) - 1)
            )
            x = round(fraction * (px - 1))
            y = round((values[index] - low) * (py - 1) / span)
            if not self.inverse:
                y = py - 1 - y
            return x, y

        for index in range(1, len(values)):
            x0, y0 = position(index - 1)
            x1, y1 = position(index)
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for step in range(steps + 1):
                x = round(x0 + (x1 - x0) * step / steps)
                y = round(y0 + (y1 - y0) * step / steps)
                grid[y // 4][x // 2] |= bits[y % 4][x % 2]
        for row, cells in enumerate(grid):
            label = (
                (low if self.inverse else high)
                if row == 0
                else (high if self.inverse else low)
                if row == rows - 1
                else None
            )
            text.append(f"{label:5g} " if label is not None else "      ", style=muted)
            text.append(
                "".join(chr(0x2800 + cell) if cell else " " for cell in cells) + "\n", style=plot
            )
        first, last = self.points[0][0][5:10], self.points[-1][0][5:10]
        text.append(f"      {first}{' ' * max(1, width - 10)}{last}", style=muted)
        return text
