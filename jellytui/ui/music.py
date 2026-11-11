"""A full-screen now-playing view for music."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, ProgressBar, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..models import TICKS_PER_SECOND, Item, format_clock
from ..player import PlayerState
from .artwork import prepare_artwork
from .capabilities import detect_graphics, image_widget_class

if TYPE_CHECKING:
    from .main import MainScreen


class MusicPlayerScreen(ModalScreen[None]):
    """A large, keyboard-driven music player."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("m", "close", "Close", show=False),
        Binding("space", "toggle_pause", "Pause"),
        Binding("left", "seek_back", "-10s"),
        Binding("right", "seek_forward", "+10s"),
        Binding("n", "next_track", "Next"),
        Binding("p", "previous_track", "Prev"),
        Binding("minus", "volume_down", "Vol -", show=False),
        Binding("equals_sign", "volume_up", "Vol +", show=False),
        Binding("s", "stop", "Stop", show=False),
    ]

    def __init__(self, controller: MainScreen) -> None:
        super().__init__()
        self.controller = controller
        self.graphics = detect_graphics()
        self._art_seq = 0
        self._art_item_id: str | None = None
        self._art_size: tuple[int, int] | None = None
        self._queue_signature: tuple[tuple[str, ...], int] | None = None
        self._last_item_id: str | None = None
        self._render_cache: tuple | None = None
        self._show_art = True

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        with Vertical(id="music-panel"):
            yield Static("NOW PLAYING", id="music-kicker")
            with Horizontal(id="music-body"):
                with Vertical(id="music-art-box"):
                    yield image_widget_class(self.graphics.mode)(id="music-art-image")
                    yield Static("", id="music-art")
                with Vertical(id="music-info"):
                    yield Static("", id="music-title")
                    yield Static("", id="music-subtitle")
                    yield Static("", id="music-state")
                    yield ProgressBar(
                        total=1,
                        show_percentage=False,
                        show_eta=False,
                        id="music-progress",
                    )
                    yield Static("", id="music-time")
                    yield Static("", id="music-controls")
            with Vertical(id="music-queue-box"):
                yield Static("QUEUE", id="music-queue-title")
                yield OptionList(id="music-queue")

    def on_mount(self) -> None:
        self._apply_layout()
        self._refresh_view(force=True)
        self.set_interval(0.5, self._refresh_view)
        queue = self.query_one("#music-queue", OptionList)
        if queue.option_count:
            queue.focus()

    def on_resize(self) -> None:
        self._apply_layout()
        self._refresh_view(force=True)

    def _apply_layout(self) -> None:
        width = self.app.size.width
        height = self.app.size.height
        self._show_art = (
            width >= 100
            and height >= 30
            and self.app.config.images
            and self.graphics.can_show_images
        )
        self.query_one("#music-art-box").display = self._show_art
        art_height = max(8, min(20, height // 3))
        image = self.query_one("#music-art-image")
        art = self.query_one("#music-art", Static)
        box = self.query_one("#music-art-box")
        image.styles.height = art_height
        art.styles.height = art_height
        box.styles.height = art_height
        self.query_one("#music-queue-box").styles.height = 9 if height >= 32 else 6
        size = (38, art_height)
        if size != self._art_size:
            self._art_size = size
            item, _ = self.controller.current_music_item()
            if item is not None:
                self._schedule_art(item, force=True)

    # ---------------------------------------------------------------- render

    def _theme_color(self, name: str) -> str:
        return str(getattr(self.app.current_theme, name, "") or "")

    def _refresh_view(self, force: bool = False) -> None:
        if not self.is_mounted:
            return
        item, index = self.controller.current_music_item()
        state = self.controller.active_player_state()
        if item is None:
            self.query_one("#music-title", Static).update(
                Text("Nothing playing", style=f"bold {self._theme_color('foreground')}")
            )
            self.query_one("#music-subtitle", Static).update(Text(""))
            self.query_one("#music-state", Static).update(Text("STOPPED", style="dim"))
            self.query_one("#music-time", Static).update(Text(""))
            self.query_one("#music-progress", ProgressBar).update(total=1, progress=0)
            self.query_one("#music-kicker", Static).update(
                Text("NOW PLAYING", style=f"bold {self._theme_color('primary')}")
            )
            self.query_one("#music-controls", Static).update(
                Text("Esc close", style="dim")
            )
            self._last_item_id = None
            self._hide_art()
            self._render_queue(item, index)
            return

        state_text = self._state_text(state)
        duration = (
            state.duration
            if state and state.duration
            else item.runtime_ticks / TICKS_PER_SECOND
        )
        position = state.position if state else 0.0
        cache = (
            item.id,
            state_text,
            round(position, 1),
            round(duration, 1),
            state.volume if state else 0,
            index,
        )
        if not force and cache == self._render_cache:
            return
        self._render_cache = cache

        foreground = self._theme_color("foreground")
        muted = self._theme_color("primary")
        self.query_one("#music-title", Static).update(
            Text(item.name, style=f"bold {foreground}")
        )
        self.query_one("#music-subtitle", Static).update(
            Text(item.detail_subtitle or "Audio", style="dim")
        )
        self.query_one("#music-state", Static).update(
            Text(state_text, style=f"bold {self._state_color(state_text)}")
        )
        time_text = f"{format_clock(position)} / {format_clock(duration)}"
        if state:
            time_text += f"   ·   volume {state.volume}%"
        self.query_one("#music-time", Static).update(Text(time_text, style="dim"))
        self.query_one("#music-progress", ProgressBar).update(
            total=max(duration, 1.0), progress=min(position, max(duration, 1.0))
        )
        self.query_one("#music-controls", Static).update(
            Text(
                "Space pause  ·  ←/→ seek  ·  n/p next/prev  ·  -/= volume  ·  s stop  ·  Esc close",
                style="dim",
            )
        )
        self.query_one("#music-kicker", Static).update(
            Text("NOW PLAYING", style=f"bold {muted}")
        )

        if item.id != self._last_item_id:
            self._last_item_id = item.id
            self._schedule_art(item, force=True)
        self._render_queue(item, index)

    def _state_text(self, state: PlayerState | None) -> str:
        if state is None:
            return "STOPPED"
        if not state.loaded and not state.paused:
            return "BUFFERING"
        if state.paused:
            return "PAUSED"
        return "PLAYING"

    def _state_color(self, state_text: str) -> str:
        if state_text == "PAUSED":
            return self._theme_color("warning")
        if state_text == "BUFFERING":
            return self._theme_color("secondary")
        if state_text == "STOPPED":
            return "dim"
        return self._theme_color("success")

    def _render_queue(self, item: Item | None, index: int) -> None:
        queue = self.controller.music_queue
        signature = (tuple(entry.id for entry in queue), index)
        if signature == self._queue_signature:
            return
        self._queue_signature = signature
        widget = self.query_one("#music-queue", OptionList)
        widget.clear_options()
        if not queue:
            widget.add_option(Option("No music queued", disabled=True))
            return
        primary = self._theme_color("primary")
        foreground = self._theme_color("foreground")
        options = []
        for position, entry in enumerate(queue):
            marker = "▶ " if position == index else "  "
            label = entry.name
            if entry.artists:
                label += f"  ·  {', '.join(entry.artists)}"
            options.append(
                Option(
                    Text.assemble(
                        (marker, f"bold {primary}"),
                        (
                            label,
                            f"bold {foreground}"
                            if position == index
                            else foreground,
                        ),
                    ),
                    id=str(position),
                )
            )
        widget.add_options(options)
        if 0 <= index < len(options):
            widget.highlighted = index

    # ---------------------------------------------------------------- actions

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle_pause(self) -> None:
        self.controller.action_toggle_pause()

    def action_seek_back(self) -> None:
        self.controller.action_seek_back()

    def action_seek_forward(self) -> None:
        self.controller.action_seek_forward()

    def action_next_track(self) -> None:
        self.controller.music_next()

    def action_previous_track(self) -> None:
        self.controller.music_previous()

    def action_volume_down(self) -> None:
        self.controller.action_volume_down()

    def action_volume_up(self) -> None:
        self.controller.action_volume_up()

    def action_stop(self) -> None:
        self.controller.action_stop_playback()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        try:
            index = int(event.option.id)
        except ValueError:
            return
        self.controller.play_music_index(index)
        self.query_one("#music-queue", OptionList).focus()

    def refresh_theme(self) -> None:
        self._render_cache = None
        self._queue_signature = None
        self._refresh_view(force=True)

    # ---------------------------------------------------------------- artwork

    def _schedule_art(self, item: Item, *, force: bool = False) -> None:
        if (
            not self._show_art
            or not self.graphics.can_show_images
            or item.artwork is None
        ):
            self._hide_art()
            return
        if not force and self._art_item_id == item.id and self._art_size is not None:
            return
        self._art_seq += 1
        self._art_item_id = item.id
        width, height = self._art_size or (38, 12)
        self._load_art(item, self._art_seq, width, height)

    @work(thread=True, exclusive=True, group="music-art")
    def _load_art(self, item: Item, seq: int, width: int, height: int) -> None:
        worker = get_current_worker()
        artwork = prepare_artwork(
            self.app.client,
            item,
            width=width,
            height=height,
            mode=self.graphics.mode,
        )
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self._art_ready, seq, item.id, artwork.path, artwork.ansi
            )

    def _art_ready(self, seq: int, item_id: str, path, art: str | None) -> None:
        if (
            seq != self._art_seq
            or not self.is_mounted
            or not self._show_art
            or self._art_item_id != item_id
        ):
            return
        if path is None:
            self._hide_art()
            return
        image = self.query_one("#music-art-image")
        art_widget = self.query_one("#music-art", Static)
        if self.graphics.mode == "chafa":
            if not art:
                self._hide_art()
                return
            art_widget.update(Text.from_ansi(art))
            art_widget.display = True
            image.display = False
        else:
            try:
                image.image = str(path)
            except Exception:
                self._hide_art()
                return
            image.display = True
            art_widget.display = False
        self.query_one("#music-art-box").display = True

    def _hide_art(self) -> None:
        if not self.is_mounted:
            return
        self._art_seq += 1
        self._art_item_id = None
        self.query_one("#music-art-box").display = False
        self.query_one("#music-art-image").image = None
        self.query_one("#music-art", Static).update("")
