"""Music queue management and queue generators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..api import JellyfinError
from ..models import format_ticks

if TYPE_CHECKING:
    from .main import MainScreen


class QueueScreen(ModalScreen[None]):
    """Inspect and edit the music queue, or build a new one."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("d", "remove", "Remove", show=False),
        Binding("delete", "remove", "Remove", show=False),
        Binding("J", "move_down", "Move down", show=False),
        Binding("K", "move_up", "Move up", show=False),
        Binding("shift+down", "move_down", "Move down", show=False),
        Binding("shift+up", "move_up", "Move up", show=False),
        Binding("s", "shuffle_queue", "Shuffle", show=False),
        Binding("l", "shuffle_library", "Library", show=False),
        Binding("g", "genre_mix", "Genres", show=False),
        Binding("x", "clear_queue", "Clear", show=False),
    ]

    def __init__(self, controller: MainScreen) -> None:
        super().__init__()
        self.controller = controller
        self._signature: tuple[tuple[str, ...], int] | None = None
        self._initialized = False

    def compose(self) -> ComposeResult:
        with Vertical(id="queue-panel"):
            yield Static("QUEUE", id="dialog-title")
            yield Static("", id="queue-summary")
            yield OptionList(id="queue-list")
            yield Static("", id="queue-status")
            yield Static(
                "Enter play  ·  d remove  ·  J/K move  ·  s shuffle queue  ·  "
                "l shuffle library  ·  g genre mix  ·  x clear  ·  Esc close",
                id="queue-hint",
            )

    def on_mount(self) -> None:
        self._refresh(force=True)
        self.set_interval(0.5, self._refresh)
        self.query_one("#queue-list", OptionList).focus()

    # ---------------------------------------------------------------- render

    def _refresh(self, force: bool = False) -> None:
        if not self.is_mounted:
            return
        queue = self.controller.music_queue
        index = self.controller.music_index
        signature = (tuple(entry.id for entry in queue), index)
        if not force and signature == self._signature:
            return
        self._signature = signature
        widget = self.query_one("#queue-list", OptionList)
        highlighted = widget.highlighted
        widget.clear_options()
        if not queue:
            widget.add_option(
                Option(
                    "The queue is empty. Add music with A in the browser, "
                    "or build one with l / g.",
                    disabled=True,
                )
            )
            self.query_one("#queue-summary", Static).update(
                Text("No tracks queued", style="bold")
            )
            self.query_one("#queue-status", Static).update(Text(""))
            self._initialized = True
            return

        primary = self.controller._theme_color("primary")
        foreground = self.controller._theme_color("foreground")
        options = []
        for position, entry in enumerate(queue):
            marker = "▶ " if position == index else "  "
            label = Text.assemble(
                (marker, f"bold {primary}"),
                (f"{position + 1:02d}  ", "dim"),
                (
                    entry.name,
                    f"bold {foreground}" if position == index else foreground,
                ),
            )
            detail = ", ".join(entry.artists)
            if entry.album:
                detail = f"{detail} · {entry.album}" if detail else entry.album
            if detail:
                label.append(f"  ·  {detail}", style="dim")
            options.append(Option(label, id=str(position)))
        widget.add_options(options)

        if not self._initialized:
            self._initialized = True
            if 0 <= index < len(queue):
                widget.highlighted = index
        elif highlighted is not None and 0 <= highlighted < len(queue):
            widget.highlighted = highlighted

        total_ticks = sum(entry.runtime_ticks for entry in queue)
        summary = Text()
        summary.append(f"{len(queue)} track{'s' if len(queue) != 1 else ''}")
        if total_ticks:
            summary.append(f"  ·  {format_ticks(total_ticks)}")
        if 0 <= index < len(queue):
            summary.append(f"  ·  current {index + 1}/{len(queue)}", style="dim")
        self.query_one("#queue-summary", Static).update(summary)

        current = queue[index] if 0 <= index < len(queue) else None
        status = Text()
        if current is not None:
            status.append("Current: ", style="dim")
            status.append(current.name)
        else:
            status.append("Nothing playing", style="dim")
        self.query_one("#queue-status", Static).update(status)

    # --------------------------------------------------------------- actions

    def _highlighted_index(self) -> int | None:
        highlighted = self.query_one("#queue-list", OptionList).highlighted
        if highlighted is None or not 0 <= highlighted < len(self.controller.music_queue):
            return None
        return highlighted

    def action_close(self) -> None:
        self.dismiss(None)

    def action_remove(self) -> None:
        index = self._highlighted_index()
        if index is None:
            return
        self.controller.remove_queue_index(index)
        self._refresh(force=True)
        if self.controller.music_queue:
            widget = self.query_one("#queue-list", OptionList)
            widget.highlighted = min(index, len(self.controller.music_queue) - 1)

    def action_move_up(self) -> None:
        index = self._highlighted_index()
        if index is None or index == 0:
            return
        self.controller.move_queue_item(index, -1)
        self._refresh(force=True)
        self.query_one("#queue-list", OptionList).highlighted = index - 1

    def action_move_down(self) -> None:
        index = self._highlighted_index()
        if index is None or index >= len(self.controller.music_queue) - 1:
            return
        self.controller.move_queue_item(index, 1)
        self._refresh(force=True)
        self.query_one("#queue-list", OptionList).highlighted = index + 1

    def action_shuffle_queue(self) -> None:
        self.controller.shuffle_queue()
        self._refresh(force=True)

    def action_shuffle_library(self) -> None:
        self.query_one("#queue-status", Static).update(
            Text("Building a music-library shuffle…", style="dim italic")
        )
        self.controller.shuffle_library()

    def action_genre_mix(self) -> None:
        self.app.push_screen(GenreScreen(self.controller))

    def action_clear_queue(self) -> None:
        self.controller.clear_queue()
        self._refresh(force=True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        try:
            index = int(event.option.id)
        except ValueError:
            return
        self.controller.play_music_index(index)


class GenreScreen(ModalScreen[None]):
    """Pick a music genre for a Jellyfin instant mix."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
    ]

    def __init__(self, controller: MainScreen) -> None:
        super().__init__()
        self.controller = controller
        self.genres: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="genre-panel"):
            yield Static("QUEUE BY GENRE", id="dialog-title")
            yield Static(
                "Choose a genre for a Jellyfin instant mix.",
                classes="dialog-subject",
            )
            yield OptionList(id="genre-list")
            yield Static("", id="genre-status")
            yield Static("Esc close", id="genre-hint")

    def on_mount(self) -> None:
        self.query_one("#genre-status", Static).update(
            Text("Loading genres…", style="dim italic")
        )
        self._load_genres()

    @work(thread=True, exclusive=True, group="genres")
    def _load_genres(self) -> None:
        worker = get_current_worker()
        client = self.app.client
        library_id = self.controller.music_library_id()
        try:
            genres = client.get_music_genres(library_id)
        except JellyfinError as exc:
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(self._failed, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(self._failed, f"Unexpected error: {exc}")
            return
        if not worker.is_cancelled and self.is_mounted:
            self.app.call_from_thread(self._loaded, genres)

    def _loaded(self, genres: list[str]) -> None:
        if not self.is_mounted:
            return
        self.genres = genres
        widget = self.query_one("#genre-list", OptionList)
        widget.clear_options()
        if not genres:
            widget.add_option(Option("No music genres were found", disabled=True))
            self.query_one("#genre-status", Static).update(
                Text("No genres available.", style="dim")
            )
            return
        widget.add_options(
            [Option(genre, id=str(index)) for index, genre in enumerate(genres)]
        )
        widget.highlighted = 0
        widget.focus()
        self.query_one("#genre-status", Static).update(
            Text(f"{len(genres)} genres", style="dim")
        )

    def _failed(self, message: str) -> None:
        if self.is_mounted:
            self.query_one("#genre-status", Static).update(
                Text(message, style=f"bold {self.app.current_theme.error}")
            )

    def action_close(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        try:
            index = int(event.option.id)
        except ValueError:
            return
        if 0 <= index < len(self.genres):
            self.controller.play_genre_mix(self.genres[index])
            self.dismiss(None)
