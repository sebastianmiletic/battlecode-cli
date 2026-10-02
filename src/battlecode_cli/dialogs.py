from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, RichLog, Static


class Confirm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, message: str, label: str = "Confirm"):
        super().__init__()
        self.heading, self.message, self.label = title, message, label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm-dialog"):
            yield Static(self.heading, classes="dialog-title", markup=False)
            with VerticalScroll(classes="confirm-copy"):
                yield Static(self.message, classes="dialog-copy", markup=False)
            with Horizontal(classes="actions"):
                yield Button("Cancel", id="cancel-confirm")
                yield Button(self.label, id="accept-confirm", classes="primary")

    def on_mount(self) -> None:
        self.query_one("#cancel-confirm").focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#cancel-confirm")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#accept-confirm")
    def accept(self) -> None:
        self.dismiss(True)


class TextViewer(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title: str, text: str):
        super().__init__()
        self.heading, self.text = title, text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog text-dialog"):
            yield Static(self.heading, classes="dialog-title", markup=False)
            yield RichLog(id="text-log", wrap=True, markup=False, highlight=False)
            yield Button("Close", id="close-text")

    def on_mount(self) -> None:
        self.query_one(RichLog).write(Text(self.text))
        self.query_one(RichLog).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#close-text")
    def close_button(self) -> None:
        self.dismiss(None)


class BotTree(DirectoryTree):
    def filter_paths(self, paths):
        return [
            p
            for p in paths
            if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() == ".zip")
        ]


class FilePicker(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, initial: str = ""):
        super().__init__()
        self.initial = initial

    def compose(self) -> ComposeResult:
        root = Path.home() / "Coding"
        if not root.is_dir():
            root = Path.home()
        with Vertical(classes="dialog file-dialog"):
            yield Static("Choose a bot ZIP or project folder", classes="dialog-title", markup=False)
            yield Input(self.initial or str(root), id="chosen-path")
            yield BotTree(root, id="file-tree")
            with Horizontal(classes="actions"):
                yield Button("Cancel", id="cancel-file")
                yield Button("Use this path", id="choose-file", classes="primary")

    @on(DirectoryTree.FileSelected)
    @on(DirectoryTree.DirectorySelected)
    def choose_path(self, event) -> None:
        self.query_one(Input).value = str(event.path)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel-file")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#choose-file")
    def accept(self) -> None:
        self.dismiss(self.query_one(Input).value.strip() or None)
