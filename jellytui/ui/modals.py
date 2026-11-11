"""Modal dialogs used by JellyTUI."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from ..models import Item, format_ticks_clock


class ResumeScreen(ModalScreen[str | None]):
    """Ask whether to resume an item or start from the beginning."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("r", "resume", "Resume", show=False),
        Binding("s", "restart", "Start over", show=False),
    ]

    def __init__(self, item: Item) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        position = format_ticks_clock(self.item.playback_position_ticks)
        duration = format_ticks_clock(self.item.runtime_ticks)
        percent = self.item.progress_percent
        with Vertical(id="dialog", classes="compact-dialog"):
            yield Static("Continue watching?", id="dialog-title")
            yield Static(Text(self.item.title), classes="dialog-subject")
            yield Static(
                f"You stopped at {position} of {duration} ({percent:.0f}%).",
                classes="dialog-body",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Resume", id="resume", variant="primary")
                yield Button("Start over", id="restart")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#resume", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume":
            self.dismiss("resume")
        elif event.button.id == "restart":
            self.dismiss("restart")
        else:
            self.dismiss(None)

    def action_resume(self) -> None:
        self.dismiss("resume")

    def action_restart(self) -> None:
        self.dismiss("restart")

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass
class TrackOption:
    key: str
    label: str
    selected: bool = False


class TrackMenuScreen(ModalScreen[str | None]):
    """Choose an audio or subtitle track."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[TrackOption], current: str | None = None):
        super().__init__()
        self.title_text = title
        self.options = options
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="track-dialog"):
            yield Static(self.title_text, id="dialog-title")
            if self.options:
                option_list = OptionList(
                    *[
                        Option(
                            Text.assemble(
                                ("● " if option.selected else "  ", "bold"),
                                (option.label, "bold" if option.selected else ""),
                            ),
                            id=option.key,
                        )
                        for option in self.options
                    ],
                    id="track-options",
                )
                yield option_list
            else:
                yield Static("No tracks available.", classes="dialog-body")
            yield Static(
                "↑/↓ choose · Enter select · Esc cancel", classes="dialog-hint"
            )

    def on_mount(self) -> None:
        if not self.options:
            return
        option_list = self.query_one(OptionList)
        option_list.focus()
        for index, option in enumerate(self.options):
            if option.selected:
                option_list.highlighted = index
                break

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Keyboard shortcut reference."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    ]

    SHORTCUTS = [
        ("Browse", ""),
        ("↑ / ↓", "Move through lists"),
        ("← / →", "Switch between sidebar and items"),
        ("Enter", "Play, or open a show/season/album"),
        ("Backspace / Esc", "Go back"),
        ("/", "Search the library"),
        ("Ctrl+R", "Refresh"),
        ("", ""),
        ("Playback", ""),
        ("Space", "Pause / resume"),
        ("← / → with Shift", "Seek 10 seconds"),
        ("[ / ]", "Seek 1 minute"),
        (", / .", "Seek 10 seconds"),
        ("- / =", "Volume down / up"),
        ("a", "Choose audio track"),
        ("v", "Choose subtitle track"),
        ("S", "Stop playback"),
        ("", ""),
        ("Other", ""),
        ("r", "Toggle watched"),
        ("i", "Toggle cover art"),
        ("L", "Log out"),
        ("q", "Quit"),
    ]

    def _help_text(self) -> Text:
        primary = str(getattr(self.app.current_theme, "primary", ""))
        text = Text()
        for key, description in self.SHORTCUTS:
            if not description:
                if key:
                    text.append(f"\n{key}\n", style=f"bold {primary}")
                else:
                    text.append("\n")
                continue
            text.append(f"  {key:<20}", style="bold")
            text.append(f"{description}\n", style="dim")
        return text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="help-dialog"):
            yield Static("Keyboard shortcuts", id="dialog-title")
            with VerticalScroll():
                yield Static(self._help_text(), id="help-text", classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    def refresh_theme(self) -> None:
        if self.is_mounted:
            self.query_one("#help-text", Static).update(self._help_text())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ErrorScreen(ModalScreen[None]):
    """A blocking error dialog for unrecoverable startup problems."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.error_title = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="compact-dialog"):
            yield Static(self.error_title, id="dialog-title")
            yield Static(Text(self.message), classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class MessageScreen(ModalScreen[None]):
    """Small informational dialog."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.message_title = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="compact-dialog"):
            yield Static(self.message_title, id="dialog-title")
            yield Static(Text(self.message), classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
