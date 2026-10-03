"""A small terminal type scale: cell-drawn titles, section labels and body text."""

from rich.text import Text
from textual.widgets import Static

from .charts import BoldDigits

FACE = {
    **BoldDigits.FACE,
    "A": ("▄▀▄", "█▀█", "█ █"),
    "B": ("█▀▄", "█▀▄", "█▄▀"),
    "C": ("█▀▀", "█  ", "█▄▄"),
    "D": ("█▀▄", "█ █", "█▄▀"),
    "E": ("█▀▀", "█▀▀", "█▄▄"),
    "F": ("█▀▀", "█▀▀", "█  "),
    "G": ("█▀▀", "█ ▄", "█▄█"),
    "H": ("█ █", "█▀█", "█ █"),
    "I": ("▀█▀", " █ ", "▄█▄"),
    "J": ("  █", "  █", "█▄█"),
    "K": ("█ █", "█▀▄", "█ █"),
    "L": ("█  ", "█  ", "█▄▄"),
    "M": ("█▄ ▄█", "█ ▀ █", "█   █"),
    "N": ("█▄ █", "█▀▄█", "█ ▀█"),
    "O": ("█▀█", "█ █", "█▄█"),
    "P": ("█▀█", "█▀▀", "█  "),
    "Q": ("█▀█", "█ █", "▀▀█"),
    "R": ("█▀█", "█▀▄", "█ █"),
    "S": ("█▀▀", "▀▀█", "▄▄█"),
    "T": ("▀█▀", " █ ", " █ "),
    "U": ("█ █", "█ █", "█▄█"),
    "V": ("█ █", "█ █", "▀▄▀"),
    "W": ("█   █", "█ ▄ █", "▀▄▀▄▀"),
    "X": ("█ █", " ▀ ", "█ █"),
    "Y": ("█ █", "▀█▀", " █ "),
    "Z": ("▀▀█", "▄▀ ", "█▄▄"),
    " ": (" ", " ", " "),
    "/": ("  █", " █ ", "█  "),
    "&": ("▄▀ ", "█▀█", "▀▄█"),
}


class PageTitle(Static):
    """Three-cell-tall headings when they fit; real text in compact terminals.

    Terminal font size is fixed. This is a text mark, not a PNG or font-size API.
    Long/non-Latin names are preserved as ordinary bold text rather than clipped.
    """

    def __init__(self, label: str, **kwargs):
        self.label = label
        super().__init__(label, markup=False, **kwargs)

    def update(self, content=""):
        self.label = content.plain if isinstance(content, Text) else str(content)
        self.tooltip = self.label
        super().update(content)

    def on_resize(self):
        self.refresh()

    def render(self) -> Text:
        label = self.label.upper()
        if label and all(character in FACE for character in label):
            lines = [" ".join(FACE[character][row] for character in label) for row in range(3)]
            if self.content_size.height >= 3 and max(map(len, lines)) <= self.content_size.width:
                return Text("\n".join(lines), style="bold")
        return Text(self.label, style="bold")


class SectionHeading(Static):
    """A quiet, ruled overline separating adjacent work sections."""

    def __init__(self, label: str, **kwargs):
        super().__init__(label.upper(), classes="section-heading", markup=False, **kwargs)
