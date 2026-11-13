"""The main browsing and playback screen."""

from __future__ import annotations

import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    ProgressBar,
    Static,
)
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..api import BROWSE_TYPES, AuthenticationError, JellyfinClient, JellyfinError
from ..audio import AudioError
from ..models import (
    AUDIO_TYPES,
    TICKS_PER_SECOND,
    Item,
    MediaSource,
    MediaStream,
    format_clock,
    format_ticks,
    format_ticks_clock,
    language_name,
)
from ..player import MpvError, MpvPlayer, PlayerState, Track
from .artwork import prepare_artwork
from .capabilities import detect_graphics, image_widget_class
from .modals import HelpScreen, ResumeScreen, TrackMenuScreen, TrackOption

SEARCH_TYPES = "Movie,Series,Episode,BoxSet,MusicAlbum,MusicArtist,Audio,MusicVideo,Video"

VIEW_TYPES: dict[str, str] = {
    "movies": "Movie,BoxSet",
    "tvshows": "Series",
    "music": "MusicArtist",
    "musicvideos": "MusicVideo",
    "boxsets": "BoxSet",
    "folders": BROWSE_TYPES,
    "homevideos": "Video,PhotoAlbum",
    "playlists": "Playlist",
    "books": "Book",
}
VIEW_SORTS: dict[str, str] = {
    "music": "SortName",
    "tvshows": "SortName",
    "movies": "SortName",
}


@dataclass
class Location:
    key: str
    title: str
    kind: str = "browse"  # resume | latest | library | children | search
    parent_id: str | None = None
    include_types: str | None = None
    sort_by: str = "SortName"
    sort_order: str = "Ascending"
    search_term: str | None = None
    recursive: bool = True


@dataclass
class PlaybackContext:
    item: Item
    source: MediaSource | None
    play_session_id: str
    start_position: float = 0.0
    external_subtitles: dict[int, MediaStream] = field(default_factory=dict)
    engine: str = "mpv"
    last_report_at: float = 0.0
    last_report_position: float = 0.0
    last_paused: bool = False
    pending_report: bool = False
    ended_count: int = 0

    def audio_index(self, state: PlayerState) -> int | None:
        if not self.source or state.aid is None:
            return None
        streams = self.source.audio_streams
        if 1 <= state.aid <= len(streams):
            return streams[state.aid - 1].index
        return None

    def subtitle_index(self, state: PlayerState) -> int | None:
        if state.sid is None:
            return None
        if state.sid in self.external_subtitles:
            return self.external_subtitles[state.sid].index
        if not self.source:
            return None
        embedded = [s for s in self.source.subtitle_streams if not s.is_external]
        if 1 <= state.sid <= len(embedded):
            return embedded[state.sid - 1].index
        return None


class MainScreen(Screen):
    """Browse the library and drive mpv playback."""

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
        Binding("slash", "focus_search", "Search"),
        Binding("space", "toggle_pause", "Pause"),
        Binding("a", "audio_tracks", "Audio"),
        Binding("v", "subtitle_tracks", "Subs"),
        Binding("m", "music_player", "Music"),
        Binding("Q", "queue", "Queue"),
        Binding("A", "add_to_queue", "Add"),
        Binding("F", "shuffle_library", "Shuffle", show=False),
        Binding("M", "instant_mix", "Mix", show=False),
        Binding("ctrl+a", "add_all_to_queue", "Add all", show=False),
        Binding("e", "edit_metadata", "Edit", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("comma", "seek_back", "-10s", show=False),
        Binding("full_stop", "seek_forward", "+10s", show=False),
        Binding("shift+left", "seek_back", "-10s", show=False),
        Binding("shift+right", "seek_forward", "+10s", show=False),
        Binding("left_square_bracket", "seek_back_minute", "-1m", show=False),
        Binding("right_square_bracket", "seek_forward_minute", "+1m", show=False),
        Binding("minus", "volume_down", "Vol -", show=False),
        Binding("equals_sign", "volume_up", "Vol +", show=False),
        Binding("S", "stop_playback", "Stop", show=False),
        Binding("r", "toggle_watched", "Watched", show=False),
        Binding("i", "toggle_images", "Art", show=False),
        Binding("L", "logout", "Logout", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
        Binding("right", "focus_items", "Items", show=False),
        Binding("left", "focus_sidebar", "Browse", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.graphics = detect_graphics()
        self.stack: list[Location] = []
        self.items: list[Item] = []
        self._views: list[Item] = []
        self._items_by_id: dict[str, Item] = {}
        self.location: Location | None = None
        self._load_seq = 0
        self._poster_seq = 0
        self._search_visible = False
        self._pre_search_stack: list[Location] = []
        self._selected_item: Item | None = None
        self._restore_selection_id: str | None = None
        self._poster_item_id: str | None = None
        self._poster_size: tuple[int, int] | None = None
        self._starting_item: Item | None = None
        self.playback: PlaybackContext | None = None
        self._last_state: PlayerState | None = None
        self._now_cache: tuple[str, str, int, float, float] | None = None
        self._quitting = False
        self._details_visible = True
        self.images_enabled = True
        self.music_queue: list[Item] = []
        self.music_index = -1

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("BROWSE", classes="section-title")
                yield OptionList(id="libraries")
            with Vertical(id="main"):
                yield Static("", id="breadcrumb")
                yield Input(
                    placeholder="Search movies, shows, music…",
                    id="search",
                )
                yield DataTable(id="items", cursor_type="row", zebra_stripes=True)
            with Vertical(id="details"):
                with Vertical(id="poster-box"):
                    yield image_widget_class(self.graphics.mode)(id="poster")
                    yield Static("", id="poster-art")
                with VerticalScroll(id="details-scroll"):
                    yield Static("", id="details-title")
                    yield Static("", id="details-meta")
                    yield Static("", id="details-body")
                with Vertical(id="track-panel"):
                    yield Static("", id="track-kicker")
                    yield Static("", id="track-title")
                    yield Static("", id="track-subtitle")
                    yield ProgressBar(
                        total=1,
                        show_percentage=False,
                        show_eta=False,
                        id="track-progress",
                    )
                    yield Static("", id="track-time")
        with Horizontal(id="nowplaying"):
            yield Static("Nothing playing", id="now-title")
            yield ProgressBar(
                total=100,
                show_percentage=False,
                show_eta=False,
                id="now-progress",
            )
            yield Static("", id="now-meta")
        yield Footer()

    def on_mount(self) -> None:
        app = self.app
        app.sub_title = f"{app.config.username} · {_server_host(app.config.server)}"
        self.images_enabled = app.config.images and self.graphics.can_show_images

        table = self.query_one("#items", DataTable)
        table.add_column("Title", key="title")
        table.add_column("Year", key="year", width=6)
        table.add_column("Length", key="length", width=9)
        table.add_column("Status", key="status", width=14)

        search = self.query_one("#search", Input)
        search.display = False

        self._apply_layout()
        self.set_interval(0.5, self._tick)

        self._load_views()
        resume = Location(key="resume", title="Continue Watching", kind="resume")
        self.stack = [resume]
        self.call_after_refresh(self._start_load, resume)

    def on_resize(self) -> None:
        self._apply_layout()

    def _apply_layout(self) -> None:
        width = self.app.size.width
        self._details_visible = width >= 110
        self.query_one("#details").display = self._details_visible
        poster_height = self._poster_height()
        poster = self.query_one("#poster")
        poster_box = self.query_one("#poster-box")
        poster_art = self.query_one("#poster-art")
        poster.styles.height = poster_height
        poster_box.styles.height = poster_height
        poster_art.styles.height = poster_height
        size = (38, poster_height)
        if (
            self._details_visible
            and self.images_enabled
            and self._selected_item is not None
            and size != self._poster_size
        ):
            self._poster_size = size
            self._schedule_poster(self._selected_item)

    def _poster_height(self) -> int:
        return max(6, min(16, self.app.size.height // 3))

    # ----------------------------------------------------------------- views

    @work(thread=True, exclusive=True, group="views")
    def _load_views(self) -> None:
        worker = get_current_worker()
        try:
            views = self.app.client.get_views()
        except AuthenticationError:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._session_expired)
            return
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.notify,
                    f"Could not load libraries: {exc}",
                    severity="error",
                    timeout=8,
                    markup=False,
                )
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.notify,
                    f"Could not load libraries: {exc}",
                    severity="error",
                    timeout=8,
                    markup=False,
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._populate_views, views)

    def _populate_views(self, views: list[Item]) -> None:
        if not self.is_mounted:
            return
        self._views = views
        primary = self._theme_color("primary")
        secondary = self._theme_color("secondary")
        self.root_locations = {
            "resume": Location(
                key="resume", title="Continue Watching", kind="resume"
            ),
            "latest": Location(key="latest", title="Recently Added", kind="latest"),
        }
        options: list[Option] = [
            Option(Text.assemble(("▶  ", f"bold {primary}"), ("Continue Watching", "")), id="resume"),
            Option(Text.assemble(("✦  ", f"bold {secondary}"), ("Recently Added", "")), id="latest"),
            Option(Text("─" * 18, style="dim"), disabled=True),
        ]
        icon_for = {
            "movies": "▣",
            "tvshows": "▤",
            "music": "♪",
            "boxsets": "❐",
            "musicvideos": "▶",
            "folders": "▸",
            "homevideos": "▸",
            "playlists": "≡",
            "books": "▥",
            "livetv": "◉",
        }
        for view in views:
            collection = view.collection_type
            location = _location_for_view(view)
            self.root_locations[location.key] = location
            icon = icon_for.get(collection, "▸")
            options.append(
                Option(
                    Text.assemble((f"{icon}  ", f"bold {secondary}"), (view.name, "")),
                    id=location.key,
                )
            )
        libraries = self.query_one("#libraries", OptionList)
        highlighted = libraries.highlighted
        libraries.clear_options()
        libraries.add_options(options)
        libraries.highlighted = min(highlighted or 0, max(0, len(options) - 1))
        self._update_breadcrumb()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if not option_id:
            return
        location = getattr(self, "root_locations", {}).get(option_id)
        if location:
            self._open_root(location)

    # ------------------------------------------------------------------ load

    def _open_root(self, location: Location) -> None:
        self.stack = [location]
        self._start_load(location)

    def _drill(self, item: Item) -> None:
        if item.type == "Series":
            include = "Season"
            sort_by = "IndexNumber"
        elif item.type == "Season":
            include = "Episode"
            sort_by = "ParentIndexNumber,IndexNumber"
        elif item.type == "MusicAlbum":
            include = "Audio"
            sort_by = "ParentIndexNumber,IndexNumber"
        elif item.type == "MusicArtist":
            include = "MusicAlbum"
            sort_by = "ProductionYear,SortName"
        elif item.type == "BoxSet":
            include = BROWSE_TYPES
            sort_by = "SortName"
        else:
            include = BROWSE_TYPES
            sort_by = "SortName"
        location = Location(
            key=f"children:{item.id}",
            title=item.name,
            kind="children",
            parent_id=item.id,
            include_types=include,
            sort_by=sort_by,
        )
        self.stack.append(location)
        self._start_load(location)

    def _start_load(self, location: Location) -> None:
        if not self.is_mounted:
            return
        self.location = location
        self._load_seq += 1
        seq = self._load_seq
        self.items = []
        self._items_by_id = {}
        self._selected_item = None
        self._hide_poster()
        table = self.query_one("#items", DataTable)
        table.clear(columns=False)
        table.loading = True
        self._update_breadcrumb()
        self.query_one("#details-title", Static).update(Text(location.title, style="bold"))
        self.query_one("#details-meta", Static).update(Text(""))
        self.query_one("#details-body", Static).update(
            Text("Loading items…", style="dim italic")
        )
        self._fetch_items(location, seq)

    @work(thread=True, exclusive=True, group="items")
    def _fetch_items(self, location: Location, seq: int) -> None:
        worker = get_current_worker()
        client: JellyfinClient = self.app.client
        try:
            if location.kind == "resume":
                items = client.get_resume()
            elif location.kind == "latest":
                items = client.get_latest()
            elif location.kind == "search":
                items = client.get_items(
                    search_term=location.search_term or "",
                    include_types=SEARCH_TYPES,
                    recursive=True,
                    sort_by="SortName",
                    limit=100,
                )
            else:
                items = client.get_items(
                    parent_id=location.parent_id,
                    include_types=location.include_types,
                    sort_by=location.sort_by,
                    sort_order=location.sort_order,
                    recursive=location.recursive,
                    limit=500,
                )
        except AuthenticationError:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._session_expired)
            return
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._load_failed, seq, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(self._load_failed, seq, f"Unexpected error: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._load_succeeded, seq, items)

    def _load_failed(self, seq: int, message: str) -> None:
        if seq != self._load_seq or not self.is_mounted:
            return
        table = self.query_one("#items", DataTable)
        table.loading = False
        self._update_breadcrumb()
        self.query_one("#details-title", Static).update(Text("Could not load items", style="bold"))
        self.query_one("#details-body", Static).update(Text(message, style="dim"))
        self.notify(message, severity="error", timeout=10, markup=False)

    def _session_expired(self) -> None:
        if self.is_mounted:
            self.query_one("#items", DataTable).loading = False
        self.notify(
            "Your Jellyfin session has expired. Press L to log in again.",
            title="Session expired",
            severity="warning",
            timeout=12,
        )

    def _load_succeeded(self, seq: int, items: list[Item]) -> None:
        if seq != self._load_seq or not self.is_mounted:
            return
        self.items = items
        self._items_by_id = {item.id: item for item in items}
        table = self.query_one("#items", DataTable)
        table.clear(columns=True)
        table.add_column(
            self.title_column,
            key="title",
        )
        table.add_column("Year", key="year", width=6)
        table.add_column("Length", key="length", width=9)
        table.add_column("Status", key="status", width=14)
        for item in items:
            table.add_row(
                Text(_row_title(item)),
                Text(str(item.production_year or "")),
                Text(format_ticks(item.runtime_ticks) if item.is_playable else ""),
                Text(item.status_text),
                key=item.id,
            )
        table.loading = False
        self._update_breadcrumb()
        if not items:
            self._clear_details("Nothing here yet.")
            self.query_one("#libraries", OptionList).focus()
            return
        index = 0
        if self._restore_selection_id:
            for position, item in enumerate(items):
                if item.id == self._restore_selection_id:
                    index = position
                    break
            self._restore_selection_id = None
        table.move_cursor(row=index)
        self._show_details(items[index])
        if self.app.screen is self:
            table.focus()

    def _update_breadcrumb(self) -> None:
        if not self.is_mounted:
            return
        parts = [location.title for location in self.stack]
        if not parts and self.location:
            parts = [self.location.title]
        label = "  /  ".join(parts) if parts else "JellyTUI"
        if self.location and self.location.kind == "search":
            label = f"Search: {self.location.search_term}"
        text = Text(label, style="bold")
        if self.items:
            text.append(f"   ·   {len(self.items)} items", style="dim")
        elif self._location_loading():
            text.append("   ·   loading…", style="dim")
        self.query_one("#breadcrumb", Static).update(text)

    def _location_loading(self) -> bool:
        try:
            return bool(self.query_one("#items", DataTable).loading)
        except Exception:
            return False

    @property
    def title_column(self) -> str:
        include = self.location.include_types if self.location else None
        if include == "MusicArtist":
            return "Artist"
        if include == "MusicAlbum":
            return "Album"
        if include == "Audio":
            return "Track"
        return "Title"

    @property
    def root_locations(self) -> dict[str, Location]:
        return getattr(self, "_root_locations", {})

    @root_locations.setter
    def root_locations(self, value: dict[str, Location]) -> None:
        self._root_locations = value

    # --------------------------------------------------------------- details

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        item = self._items_by_id.get(str(event.row_key.value))
        if item:
            self._show_details(item)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        item = self._items_by_id.get(str(event.row_key.value))
        if not item:
            return
        if item.is_browsable:
            self._drill(item)
        elif item.is_playable:
            self._play_item(item)
        else:
            self.notify("This item cannot be played.", severity="warning")

    def _show_details(self, item: Item, *, refresh_poster: bool = True) -> None:
        if not self.is_mounted:
            return
        self._selected_item = item
        self.query_one("#details-title", Static).update(Text(item.title, style="bold"))
        subtitle = item.detail_subtitle or item.type
        self.query_one("#details-meta", Static).update(Text(subtitle, style="dim"))

        body = Text()
        if item.played:
            body.append(
                "✓ Watched\n", style=f"bold {self._theme_color('success')}"
            )
        elif item.progress_percent >= 0.5:
            position = format_ticks_clock(item.playback_position_ticks)
            body.append(
                f"Resume at {position}  ·  {item.progress_percent:.0f}% watched\n",
                style=f"bold {self._theme_color('warning')}",
            )
        if item.overview:
            body.append("\n")
            body.append(item.overview.strip())
            body.append("\n")
        if item.genres:
            body.append("\n")
            body.append(
                " · ".join(item.genres),
                style=f"italic {self._theme_color('accent')}",
            )
            body.append("\n")
        source = item.media_source
        if source:
            body.append("\n")
            facts = [source.container.upper() or "MEDIA"]
            audio_count = len(source.audio_streams)
            subtitle_count = len(source.subtitle_streams)
            if audio_count:
                facts.append(f"{audio_count} audio track{'s' if audio_count != 1 else ''}")
            if subtitle_count:
                facts.append(
                    f"{subtitle_count} subtitle track{'s' if subtitle_count != 1 else ''}"
                )
            body.append("  ·  ".join(facts), style="dim")
            body.append("\n")
        if item.is_browsable:
            hint = "Enter opens this item."
        elif item.is_playable:
            if item.type in AUDIO_TYPES:
                hint = "Enter plays this track."
            else:
                hint = "Enter starts playback."
        else:
            hint = ""
        if hint:
            body.append("\n" + hint, style="italic dim")
        self.query_one("#details-body", Static).update(body)
        if refresh_poster:
            self._schedule_poster(item)

    def _theme_color(self, name: str) -> str:
        return str(getattr(self.app.current_theme, name, "") or "")

    def refresh_theme(self) -> None:
        if not self.is_mounted:
            return
        if self._views:
            self._populate_views(self._views)
        if self._selected_item is not None:
            self._show_details(self._selected_item, refresh_poster=False)

    def _clear_details(self, message: str) -> None:
        if not self.is_mounted:
            return
        self._selected_item = None
        self._hide_poster()
        self.query_one("#details-title", Static).update(Text(""))
        self.query_one("#details-meta", Static).update(Text(""))
        self.query_one("#details-body", Static).update(Text(message, style="dim italic"))

    # ----------------------------------------------------------------- images

    def _schedule_poster(self, item: Item) -> None:
        artwork = item.artwork
        if (
            not self.images_enabled
            or not self._details_visible
            or artwork is None
            or not self.graphics.can_show_images
        ):
            self._hide_poster()
            return
        height = self._poster_height()
        self._poster_size = (38, height)
        self._poster_seq += 1
        self._poster_item_id = item.id
        self._fetch_poster(item, self._poster_seq, 38, height)

    @work(thread=True, exclusive=True, group="poster")
    def _fetch_poster(
        self, item: Item, seq: int, width: int, height: int
    ) -> None:
        worker = get_current_worker()
        artwork = prepare_artwork(
            self.app.client,
            item,
            width=width,
            height=height,
            mode=self.graphics.mode,
        )
        if worker.is_cancelled:
            return
        self.app.call_from_thread(
            self._poster_ready, seq, item.id, artwork.path, artwork.ansi
        )

    def _poster_ready(
        self, seq: int, item_id: str, path: Path | None, art: str | None
    ) -> None:
        if (
            seq != self._poster_seq
            or self._poster_item_id != item_id
            or not self.is_mounted
            or not self.images_enabled
            or not self._details_visible
        ):
            return
        if path is None:
            self._hide_poster()
            return
        poster = self.query_one("#poster")
        art_widget = self.query_one("#poster-art", Static)
        if self.graphics.mode == "chafa":
            if not art:
                self._hide_poster()
                return
            art_widget.update(Text.from_ansi(art))
            art_widget.display = True
            poster.display = False
        else:
            try:
                poster.image = str(path)
            except Exception:
                self._hide_poster()
                return
            poster.display = True
            art_widget.display = False
        self.query_one("#poster-box").display = True

    def _hide_poster(self) -> None:
        if not self.is_mounted:
            return
        self._poster_seq += 1
        self.query_one("#poster-box").display = False
        self.query_one("#poster").image = None
        self.query_one("#poster-art", Static).update("")

    # --------------------------------------------------------------- playback

    def active_player_state(self) -> PlayerState | None:
        """State for whichever backend is currently playing."""
        context = self.playback
        if context is None:
            return None
        return self._engine_state(context)

    def _engine(self, context: PlaybackContext | None = None):
        context = context or self.playback
        if context is not None and context.engine == "audio":
            return self.app.audio_player
        return self.app.player

    def _engine_state(self, context: PlaybackContext | None = None) -> PlayerState:
        return self._engine(context).state()

    def _prepare_music_queue(self, item: Item) -> None:
        if item.type not in AUDIO_TYPES:
            self.music_queue = []
            self.music_index = -1
            return
        audio_items = [entry for entry in self.items if entry.type in AUDIO_TYPES]
        ids = [entry.id for entry in audio_items]
        if item.id in ids:
            self.music_queue = audio_items
            self.music_index = ids.index(item.id)
        else:
            self.music_queue = [item]
            self.music_index = 0

    def current_music_item(self) -> tuple[Item | None, int]:
        context = self.playback
        if context and context.item.type in AUDIO_TYPES:
            return context.item, self.music_index
        if 0 <= self.music_index < len(self.music_queue):
            return self.music_queue[self.music_index], self.music_index
        return None, -1

    def play_music_index(self, index: int) -> None:
        if not (0 <= index < len(self.music_queue)):
            return
        self.music_index = index
        self._begin_playback(self.music_queue[index], 0.0)

    def music_next(self) -> None:
        if not self.music_queue:
            self.notify("No music is queued.", timeout=3)
            return
        next_index = self.music_index + 1
        if next_index >= len(self.music_queue):
            self.notify("End of the music queue.", timeout=3)
            return
        self.play_music_index(next_index)

    def music_previous(self) -> None:
        if not self.music_queue:
            self.notify("No music is queued.", timeout=3)
            return
        state = self.active_player_state()
        if state is not None and state.position > 5.0:
            self._player_command(
                self._engine().seek, 0.0, absolute=True, error_title="Seek failed"
            )
            return
        if self.music_index > 0:
            self.play_music_index(self.music_index - 1)
        elif state is not None:
            self._player_command(
                self._engine().seek, 0.0, absolute=True, error_title="Seek failed"
            )

    def action_edit_metadata(self) -> None:
        item = self._selected()
        if item:
            self.edit_metadata(item)

    def edit_current_metadata(self) -> None:
        item, _ = self.current_music_item()
        if item is not None:
            self.edit_metadata(item)

    def edit_metadata(self, item: Item) -> None:
        if self.app.client is None:
            return
        from .metadata import MetadataScreen

        item_id = item.id
        self.app.push_screen(
            MetadataScreen(item),
            callback=lambda result: self._metadata_saved(item_id, result),
        )

    def _metadata_saved(self, item_id: str, result: Item | bool | None) -> None:
        if result is None:
            return
        if result is True:
            self._restore_selection_id = item_id
            if self.location:
                self._start_load(self.location)
            for screen in self.app.screen_stack:
                refresh = getattr(screen, "refresh_content", None)
                if callable(refresh):
                    refresh()
            self.notify("Metadata lookup applied", timeout=3, markup=False)
            return
        updated = result
        if self.playback and self.playback.item.id == updated.id:
            _copy_item_fields(self.playback.item, updated)
        for entry in self.music_queue:
            if entry.id == updated.id:
                _copy_item_fields(entry, updated)
        self._restore_selection_id = updated.id
        if self.location:
            self._start_load(self.location)
        for screen in self.app.screen_stack:
            refresh = getattr(screen, "refresh_content", None)
            if callable(refresh):
                refresh()
        self.notify(f"Metadata saved: {updated.name}", timeout=3, markup=False)

    # ------------------------------------------------------------------ queue

    def music_library_id(self) -> str | None:
        for view in self._views:
            if view.collection_type == "music":
                return view.id
        return None

    def action_queue(self) -> None:
        if self.app.client is None:
            return
        from .queue import QueueScreen

        self.app.push_screen(QueueScreen(self))

    def action_add_to_queue(self) -> None:
        item = self._selected()
        if item is None:
            return
        if not _is_queuable(item):
            self.notify(
                "Select a track, album or artist to add to the queue.",
                timeout=4,
            )
            return
        self._add_item_to_queue(item)

    @work(thread=True, exclusive=False, group="queue-add")
    def _add_item_to_queue(self, item: Item) -> None:
        worker = get_current_worker()
        try:
            tracks = _expand_music_item(self.app.client, item)
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error, "Could not add to the queue", str(exc)
                )
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error,
                    "Could not add to the queue",
                    f"Unexpected error: {exc}",
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self._append_queue_items, tracks, item.name
            )

    def action_add_all_to_queue(self) -> None:
        if self.location is None:
            return
        self._add_all_to_queue(self.location, list(self.items))

    @work(thread=True, exclusive=False, group="queue-add")
    def _add_all_to_queue(self, location: Location, items: list[Item]) -> None:
        worker = get_current_worker()
        client: JellyfinClient = self.app.client
        include = location.include_types or ""
        parent_id = location.parent_id
        music_parent = parent_id is not None and include in {
            "MusicArtist",
            "MusicAlbum",
            "Audio",
            "AudioBook",
        }
        try:
            if location.kind == "search" or not music_parent:
                tracks: list[Item] = []
                seen: set[str] = set()
                for item in items:
                    for track in _expand_music_item(client, item):
                        if track.id not in seen:
                            seen.add(track.id)
                            tracks.append(track)
            else:
                sort_by = (
                    "ParentIndexNumber,IndexNumber"
                    if include in {"Audio", "AudioBook"}
                    else "Album,ParentIndexNumber,IndexNumber"
                )
                tracks = client.get_music_tracks(
                    parent_id, recursive=True, sort_by=sort_by
                )
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error, "Could not add to the queue", str(exc)
                )
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error,
                    "Could not add to the queue",
                    f"Unexpected error: {exc}",
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self._append_queue_items, tracks, location.title
            )

    def _append_queue_items(self, items: list[Item], label: str) -> None:
        known = {entry.id for entry in self.music_queue}
        added: list[Item] = []
        for item in items:
            if item.type not in AUDIO_TYPES or item.id in known:
                continue
            known.add(item.id)
            added.append(item)
        if not added:
            self.notify(
                f"No new tracks to add from {label}.",
                timeout=4,
                markup=False,
            )
            return
        was_empty = not self.music_queue
        self.music_queue.extend(added)
        if was_empty:
            self.music_index = 0
        count = len(added)
        self.notify(
            f"Added {count} track{'s' if count != 1 else ''} from {label} "
            "to the queue. Press Q to manage it.",
            timeout=5,
            markup=False,
        )

    def action_shuffle_library(self) -> None:
        self.shuffle_library()

    def shuffle_library(self) -> None:
        library = self.music_library_id()
        if library is None:
            self.notify(
                "No music library is available to shuffle.",
                severity="warning",
                timeout=4,
            )
            return
        self.notify("Building a music-library shuffle…", timeout=3)
        self._shuffle_library_worker(library)

    @work(thread=True, exclusive=True, group="queue-generate")
    def _shuffle_library_worker(self, library_id: str) -> None:
        worker = get_current_worker()
        try:
            tracks = self.app.client.get_music_tracks(
                library_id, recursive=True, sort_by="Random", limit=2000
            )
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error, "Could not shuffle the library", str(exc)
                )
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error,
                    "Could not shuffle the library",
                    f"Unexpected error: {exc}",
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self._replace_queue, tracks, "library shuffle"
            )

    def action_instant_mix(self) -> None:
        item = self._selected()
        if item is None or item.type not in {
            "Audio",
            "AudioBook",
            "MusicAlbum",
            "MusicArtist",
        }:
            self.notify(
                "Select a track, album or artist to start a mix.",
                timeout=4,
            )
            return
        self.notify(f"Building a mix from {item.name}…", timeout=3)
        self._instant_mix_worker(item)

    @work(thread=True, exclusive=True, group="queue-generate")
    def _instant_mix_worker(self, item: Item) -> None:
        worker = get_current_worker()
        try:
            tracks = self.app.client.instant_mix(item.id)
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error, "Could not build the mix", str(exc)
                )
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error,
                    "Could not build the mix",
                    f"Unexpected error: {exc}",
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self._replace_queue, tracks, f"{item.name} mix"
            )

    def play_genre_mix(self, genre: str) -> None:
        self.notify(f"Building a {genre} mix…", timeout=3)
        self._genre_mix_worker(genre)

    @work(thread=True, exclusive=True, group="queue-generate")
    def _genre_mix_worker(self, genre: str) -> None:
        worker = get_current_worker()
        try:
            tracks = self.app.client.genre_mix(genre)
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error, f"Could not build a {genre} mix", str(exc)
                )
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._queue_error,
                    f"Could not build a {genre} mix",
                    f"Unexpected error: {exc}",
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self._replace_queue, tracks, f"{genre} mix"
            )

    def _replace_queue(self, items: list[Item], label: str) -> None:
        tracks = [item for item in items if item.type in AUDIO_TYPES]
        if not tracks:
            self.notify(
                f"No playable music was found for the {label}.",
                severity="warning",
                timeout=5,
                markup=False,
            )
            return
        self.music_queue = tracks
        self.music_index = 0
        self.play_music_index(0)
        self.notify(
            f"Queued {len(tracks)} tracks from the {label}.",
            timeout=4,
            markup=False,
        )

    def _queue_error(self, title: str, message: str) -> None:
        self.notify(
            message,
            title=title,
            severity="error",
            timeout=8,
            markup=False,
        )

    def remove_queue_index(self, index: int) -> None:
        if not 0 <= index < len(self.music_queue):
            return
        was_current = index == self.music_index
        was_playing = (
            self.playback is not None
            and self.playback.item.type in AUDIO_TYPES
        )
        self.music_queue.pop(index)
        if index < self.music_index:
            self.music_index -= 1
        elif was_current:
            if index < len(self.music_queue):
                # Playing the replacement lets the normal start-playback path
                # stop the removed track without racing a separate stop call.
                self.music_index = index
                if was_playing:
                    self.play_music_index(index)
            elif self.music_queue:
                self.music_index = len(self.music_queue) - 1
                if was_playing:
                    self.action_stop_playback()
            else:
                self.music_index = -1
                if was_playing:
                    self.action_stop_playback()

    def move_queue_item(self, index: int, delta: int) -> None:
        target = index + delta
        if not 0 <= index < len(self.music_queue):
            return
        if not 0 <= target < len(self.music_queue):
            return
        current_id = self._queue_current_id()
        self.music_queue[index], self.music_queue[target] = (
            self.music_queue[target],
            self.music_queue[index],
        )
        self._restore_queue_index(current_id)

    def shuffle_queue(self) -> None:
        if len(self.music_queue) < 2:
            self.notify(
                "The queue needs at least two tracks to shuffle.",
                timeout=3,
            )
            return
        current_id = self._queue_current_id()
        random.shuffle(self.music_queue)
        self._restore_queue_index(current_id)
        self.notify("Shuffled the queue.", timeout=3)

    def clear_queue(self) -> None:
        if not self.music_queue:
            return
        if (
            self.playback is not None
            and self.playback.item.type in AUDIO_TYPES
        ):
            self.action_stop_playback()
        self.music_queue.clear()
        self.music_index = -1
        self.notify("Cleared the music queue.", timeout=3)

    def _queue_current_id(self) -> str | None:
        if 0 <= self.music_index < len(self.music_queue):
            return self.music_queue[self.music_index].id
        return None

    def _restore_queue_index(self, item_id: str | None) -> None:
        if item_id is None:
            return
        for position, entry in enumerate(self.music_queue):
            if entry.id == item_id:
                self.music_index = position
                return

    def action_music_player(self) -> None:
        item, _ = self.current_music_item()
        has_music = bool(
            item
            or (self.playback and self.playback.item.type in AUDIO_TYPES)
            or self.music_queue
        )
        if not has_music:
            self.notify("No music is playing or queued.", timeout=3)
            return
        from .music import MusicPlayerScreen

        self.app.push_screen(MusicPlayerScreen(self))

    def _play_item(self, item: Item) -> None:
        self._prepare_music_queue(item)
        start = 0.0
        duration = item.runtime_ticks / TICKS_PER_SECOND if item.runtime_ticks else 0.0
        position = item.resume_seconds
        if (
            not item.played
            and position >= 30
            and (duration <= 0 or position < duration - 60)
        ):
            self.app.push_screen(
                ResumeScreen(item),
                callback=lambda choice: self._resume_choice(item, choice),
            )
            return
        self._begin_playback(item, start)

    def _highlight_table_item(self, item_id: str) -> None:
        if item_id not in self._items_by_id:
            return
        table = self.query_one("#items", DataTable)
        for index, entry in enumerate(self.items):
            if entry.id == item_id:
                table.move_cursor(row=index)
                return

    def _resume_choice(self, item: Item, choice: str | None) -> None:
        if choice == "resume":
            self._begin_playback(item, item.resume_seconds)
        elif choice == "restart":
            self._begin_playback(item, 0.0)

    def _begin_playback(self, item: Item, start_seconds: float) -> None:
        if not self.is_mounted:
            return
        previous = self.playback
        self.playback = None
        self._starting_item = item
        self._highlight_table_item(item.id)
        self._set_now(
            item.title,
            "Starting playback…",
            0,
            0.0,
            0.0,
        )
        self._start_playback(item, start_seconds, previous)

    @work(thread=True, exclusive=True, group="playback")
    def _start_playback(
        self, item: Item, start_seconds: float, previous: PlaybackContext | None
    ) -> None:
        worker = get_current_worker()
        client: JellyfinClient = self.app.client
        player: MpvPlayer = self.app.player
        audio = self.app.audio_player
        try:
            if previous:
                previous_state = self._engine_state(previous)
                if self.app.config.report_progress:
                    _report_stopped_quietly(client, previous, previous_state)
                if previous.engine == "audio":
                    audio.stop()
                else:
                    player.stop()
            source = item.media_source
            if source is None or not source.streams:
                refreshed = client.get_item(item.id)
                if refreshed.media_sources:
                    item = refreshed
                    source = item.media_source
            if source is None:
                raise JellyfinError("No playable media source was found for this item.")
            play_session_id = str(uuid.uuid4())
            url = client.stream_url(item, source, play_session_id=play_session_id)
            context: PlaybackContext | None = None

            if item.type in AUDIO_TYPES and self.app.config.audio_backend != "mpv":
                usable, reason = audio.availability()
                if usable:
                    try:
                        audio.load(
                            url,
                            start=start_seconds,
                            duration=item.runtime_ticks / TICKS_PER_SECOND,
                            title=item.title,
                        )
                        state = audio.state()
                        context = PlaybackContext(
                            item=item,
                            source=source,
                            play_session_id=play_session_id,
                            start_position=start_seconds,
                            engine="audio",
                            ended_count=state.end_count,
                        )
                        context.last_paused = state.paused
                    except AudioError as exc:
                        audio.stop()
                        self.app.call_from_thread(
                            self.notify,
                            f"Music backend failed ({exc}); falling back to mpv.",
                            severity="warning",
                            timeout=6,
                            markup=False,
                        )
                        context = None
                elif self.app.config.audio_backend == "ffmpeg":
                    self.app.call_from_thread(
                        self.notify,
                        f"Music backend unavailable ({reason}); using mpv.",
                        severity="warning",
                        timeout=6,
                        markup=False,
                    )

            if context is None:
                player.ensure_started()
                player.load(url, start=start_seconds)
                try:
                    player.set_property("force-media-title", f"{item.title} — JellyTUI")
                except MpvError:
                    pass
                state = player.state()
                context = PlaybackContext(
                    item=item,
                    source=source,
                    play_session_id=play_session_id,
                    start_position=start_seconds,
                    engine="mpv",
                    ended_count=state.end_count,
                )
                context.last_paused = state.paused
                self._apply_track_preferences(context)
                state = player.state()

            if self.app.config.report_progress:
                try:
                    client.report_start(
                        item,
                        source,
                        play_session_id=play_session_id,
                        position_ticks=int(max(0.0, state.position) * 10_000_000),
                        audio_index=context.audio_index(state),
                        subtitle_index=context.subtitle_index(state),
                        paused=state.paused,
                    )
                except JellyfinError:
                    pass
        except (JellyfinError, MpvError, AudioError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._playback_failed, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled:
                self.app.call_from_thread(self._playback_failed, f"Playback failed: {exc}")
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._playback_ready, context)

    def _apply_track_preferences(self, context: PlaybackContext) -> None:
        """Apply configured audio/subtitle language preferences after loading."""
        player: MpvPlayer = self.app.player
        state = player.state()
        config = self.app.config
        wanted_audio = config.audio_language.strip()
        if wanted_audio:
            track = _find_track(state.audio_tracks, wanted_audio)
            if track:
                try:
                    player.set_audio(track.id)
                except MpvError:
                    pass
        mode = (config.subtitle_mode or "auto").lower()
        if mode == "off":
            try:
                player.set_subtitle(None)
                context.pending_report = True
            except MpvError:
                pass
            return
        wanted_subtitle = config.subtitle_language.strip()
        if mode == "forced":
            forced = next((track for track in state.subtitle_tracks if track.forced), None)
            if forced:
                try:
                    player.set_subtitle(forced.id)
                except MpvError:
                    pass
            else:
                try:
                    player.set_subtitle(None)
                except MpvError:
                    pass
            return
        if not wanted_subtitle or not context.source:
            return
        track = _find_track(state.subtitle_tracks, wanted_subtitle)
        if track:
            try:
                player.set_subtitle(track.id)
            except MpvError:
                pass
            return
        external = next(
            (
                stream
                for stream in context.source.external_subtitles
                if _language_matches(stream.language, wanted_subtitle)
                and stream.can_stream_as_file
            ),
            None,
        )
        if external is None:
            return
        try:
            url = self.app.client.subtitle_url(
                context.item.id, context.source, external.index, external.codec
            )
            track_id = player.add_subtitle(
                url,
                title=external.display_title or external.label,
                lang=external.language,
            )
            context.external_subtitles[track_id] = external
            context.pending_report = True
        except (MpvError, JellyfinError):
            pass

    def _playback_ready(self, context: PlaybackContext) -> None:
        if not self.is_mounted:
            return
        self._starting_item = None
        self.playback = context
        self._last_state = self._engine_state(context)
        self._now_cache = None
        self.notify(f"Playing {context.item.title}", timeout=3, markup=False)

    def _playback_failed(self, message: str) -> None:
        self._starting_item = None
        self.playback = None
        self._set_now_idle("Nothing playing", "Playback failed")
        self.notify(message, title="Playback failed", severity="error", timeout=10, markup=False)
        if self.location:
            _ = self.location  # keep browsing state; no reload needed

    def _finish_playback(self, context: PlaybackContext, state: PlayerState) -> None:
        if self.playback is not context:
            return
        self.playback = None
        self._now_cache = None
        eof = state.end_reason == "eof"
        error = state.error if state.end_reason == "error" else None
        position = state.duration if eof and state.duration else state.position
        backend = "Audio backend" if context.engine == "audio" else "mpv"

        next_item = None
        if (
            eof
            and context.item.type in AUDIO_TYPES
            and 0 <= self.music_index < len(self.music_queue) - 1
        ):
            self.music_index += 1
            next_item = self.music_queue[self.music_index]

        if error:
            self.notify(
                f"{backend} reported an error: {error}",
                title="Playback error",
                severity="error",
                timeout=8,
                markup=False,
            )
        self._finalize_playback(context, position, eof=eof)

        if next_item is not None:
            self._begin_playback(next_item, 0.0)
            return

        self._set_now_idle(
            f"Finished {context.item.title}" if eof else "Stopped",
            "" if eof else context.item.title,
        )
        self._restore_selection_id = context.item.id
        if self.location:
            self._start_load(self.location)

    @work(thread=True, exclusive=True, group="finalize")
    def _finalize_playback(
        self, context: PlaybackContext, position: float, *, eof: bool
    ) -> None:
        if not self.app.config.report_progress:
            return
        client: JellyfinClient = self.app.client
        try:
            client.report_stopped(
                context.item,
                context.source,
                play_session_id=context.play_session_id,
                position_ticks=int(max(0.0, position) * 10_000_000),
            )
            if eof and context.item.is_playable:
                client.mark_played(context.item.id, True)
        except JellyfinError:
            pass

    def _playback_lost(self, message: str, position: float | None = None) -> None:
        context = self.playback
        self.playback = None
        self._now_cache = None
        self._set_now_idle("Nothing playing", "")
        if context:
            self.notify(message, severity="error", timeout=8, markup=False)
            last_position = max(
                context.last_report_position, position or 0.0
            )
            self._finalize_playback(context, last_position, eof=False)

    def _tick(self) -> None:
        if not self.is_mounted:
            return
        context = self.playback
        if context is None:
            self._hide_track_panel()
            return
        state = self._engine_state(context)
        self._last_state = state
        self._update_track_panel(context, state)
        if not state.running:
            message = (
                "The audio backend stopped unexpectedly."
                if context.engine == "audio"
                else "mpv exited unexpectedly."
            )
            self._playback_lost(message, position=state.position)
            return
        if state.end_count > context.ended_count:
            context.ended_count = state.end_count
            if not state.loaded:
                self._finish_playback(context, state)
                return
        if state.loaded:
            self._update_now_playing(context, state)
            position = max(0.0, state.position)
            now = time.monotonic()
            if (
                context.pending_report
                or state.paused != context.last_paused
                or abs(position - context.last_report_position) >= 10
                or now - context.last_report_at >= 10
            ):
                self._send_progress(context, state)
        else:
            self._set_now(
                context.item.title,
                "Buffering…",
                0,
                0.0,
                0.0,
            )

    def _send_progress(self, context: PlaybackContext, state: PlayerState) -> None:
        if not self.app.config.report_progress:
            return
        context.last_report_at = time.monotonic()
        context.last_report_position = max(0.0, state.position)
        context.last_paused = state.paused
        context.pending_report = False
        if self.app.config.report_progress:
            self._report_progress_worker(
                context,
                position=context.last_report_position,
                paused=state.paused,
                audio_index=context.audio_index(state),
                subtitle_index=context.subtitle_index(state),
                volume=state.volume,
            )

    @work(thread=True, exclusive=True, group="progress")
    def _report_progress_worker(
        self,
        context: PlaybackContext,
        *,
        position: float,
        paused: bool,
        audio_index: int | None,
        subtitle_index: int | None,
        volume: int | None = None,
    ) -> None:
        client: JellyfinClient = self.app.client
        try:
            client.report_progress(
                context.item,
                context.source,
                play_session_id=context.play_session_id,
                position_ticks=int(max(0.0, position) * 10_000_000),
                audio_index=audio_index,
                subtitle_index=subtitle_index,
                paused=paused,
                volume=volume,
            )
        except JellyfinError:
            pass

    def _update_track_panel(
        self, context: PlaybackContext, state: PlayerState
    ) -> None:
        panel = self.query_one("#track-panel")
        if context.item.type not in AUDIO_TYPES:
            panel.display = False
            return
        panel.display = True
        duration = max(0.0, state.duration or 0.0)
        position = max(0.0, state.position or 0.0)
        if not state.loaded:
            status = "buffering"
        elif state.paused:
            status = "paused"
        else:
            status = "playing"
        backend = "ffmpeg" if context.engine == "audio" else "mpv"
        kicker = f"NOW PLAYING · {backend.upper()} · {status.upper()}"
        self.query_one("#track-kicker", Static).update(
            Text(kicker, style=f"bold {self._theme_color('primary')}")
        )
        self.query_one("#track-title", Static).update(
            Text(context.item.name, style=f"bold {self._theme_color('foreground')}")
        )
        self.query_one("#track-subtitle", Static).update(
            Text(context.item.detail_subtitle or "Audio", style="dim")
        )
        self.query_one("#track-time", Static).update(
            Text(
                f"{format_clock(position)} / {format_clock(duration)}"
                f"   ·   volume {state.volume}%",
                style="dim",
            )
        )
        self.query_one("#track-progress", ProgressBar).update(
            total=max(duration, 1.0),
            progress=min(position, max(duration, 1.0)),
        )

    def _hide_track_panel(self) -> None:
        try:
            self.query_one("#track-panel").display = False
        except Exception:
            pass

    def _update_now_playing(self, context: PlaybackContext, state: PlayerState) -> None:
        duration = max(0.0, state.duration or 0.0)
        position = max(0.0, state.position or 0.0)
        meta = f"{format_clock(position)} / {format_clock(duration)}"
        meta = ("paused · " if state.paused else "") + meta
        self._set_now(context.item.title, meta, position, duration, duration)

    def _set_now(
        self,
        title: str,
        meta: str,
        position: float,
        duration: float,
        total: float,
    ) -> None:
        cache = (title, meta, int(position), round(duration, 1), round(total, 1))
        if cache == self._now_cache:
            return
        self._now_cache = cache
        self.query_one("#now-title", Static).update(Text(title))
        self.query_one("#now-meta", Static).update(Text(meta, style="dim"))
        bar = self.query_one("#now-progress", ProgressBar)
        bar.update(total=max(total, 1.0), progress=min(position, max(total, 1.0)))

    def _set_now_idle(self, title: str, meta: str) -> None:
        if self._now_cache and self._now_cache[0] == title and self._now_cache[1] == meta:
            return
        self._now_cache = None
        self._set_now(title, meta, 0.0, 0.0, 0.0)
        bar = self.query_one("#now-progress", ProgressBar)
        bar.update(total=100, progress=0)

    # ---------------------------------------------------------------- actions

    def _selected(self) -> Item | None:
        table = self.query_one("#items", DataTable)
        if table.cursor_row < 0 or table.cursor_row >= len(self.items):
            return None
        return self.items[table.cursor_row]

    def action_play_selected(self) -> None:
        item = self._selected()
        if item:
            self._play_item(item)

    def action_toggle_pause(self) -> None:
        if not self.playback:
            if self._starting_item:
                return
            self.notify("Nothing is playing.", timeout=3)
            return
        self._player_command(self._engine().toggle_pause, error_title="Pause failed")

    @work(thread=True, exclusive=True, group="control")
    def _player_command(
        self,
        function: Callable,
        *args,
        error_title: str = "Playback error",
        **kwargs,
    ) -> None:
        worker = get_current_worker()
        try:
            function(*args, **kwargs)
        except (MpvError, AudioError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.notify,
                    str(exc),
                    title=error_title,
                    severity="error",
                    timeout=5,
                    markup=False,
                )

    def _seek(self, offset: float) -> None:
        if not self.playback:
            return
        self.playback.pending_report = True
        self._player_command(self._engine().seek, offset, error_title="Seek failed")

    def action_seek_back(self) -> None:
        self._seek(-10)

    def action_seek_forward(self) -> None:
        self._seek(10)

    def action_seek_back_minute(self) -> None:
        self._seek(-60)

    def action_seek_forward_minute(self) -> None:
        self._seek(60)

    def _volume(self, delta: int) -> None:
        state = self._last_state or self._engine_state()
        limit = 100 if self.playback and self.playback.engine == "audio" else 130
        volume = max(0, min(limit, state.volume + delta))
        self._player_command(
            self._engine().set_volume, volume, error_title="Volume failed"
        )
        self.notify(f"Volume {volume}%", timeout=1)

    def action_volume_down(self) -> None:
        self._volume(-5)

    def action_volume_up(self) -> None:
        self._volume(5)

    def action_stop_playback(self) -> None:
        context = self.playback
        if not context:
            return
        state = self._engine_state(context)
        self.playback = None
        self._now_cache = None
        self._set_now_idle("Stopped", context.item.title)
        self._finalize_playback(context, max(0.0, state.position), eof=False)
        self._player_command(self._engine(context).stop, error_title="Stop failed")
        self._restore_selection_id = context.item.id
        if self.location:
            self._start_load(self.location)

    def action_audio_tracks(self) -> None:
        context = self.playback
        if not context:
            self.notify("Nothing is playing.", timeout=3)
            return
        if context.engine == "audio":
            self.notify(
                "Audio track selection is not available with the ffmpeg music backend.",
                timeout=4,
            )
            return
        state = self._last_state or self._engine_state(context)
        options = [
            TrackOption(
                key=f"aid:{track.id}",
                label=track.label,
                selected=track.id == state.aid,
            )
            for track in state.audio_tracks
        ]
        self.app.push_screen(
            TrackMenuScreen("Audio track", options),
            callback=self._audio_track_chosen,
        )

    def _audio_track_chosen(self, key: str | None) -> None:
        if not key or not key.startswith("aid:"):
            return
        context = self.playback
        track_id = int(key.split(":", 1)[1])
        self._player_command(self.app.player.set_audio, track_id, error_title="Audio track failed")
        if context:
            context.pending_report = True

    def action_subtitle_tracks(self) -> None:
        context = self.playback
        if not context:
            self.notify("Nothing is playing.", timeout=3)
            return
        if context.engine == "audio":
            self.notify(
                "Subtitle selection is not available with the ffmpeg music backend.",
                timeout=4,
            )
            return
        state = self._last_state or self._engine_state(context)
        options = [
            TrackOption(
                key="sid:off",
                label="Off",
                selected=state.sid is None,
            )
        ]
        options.extend(
            TrackOption(
                key=f"sid:{track.id}",
                label=track.label,
                selected=track.id == state.sid,
            )
            for track in state.subtitle_tracks
        )
        loaded_streams = {
            stream.index for stream in context.external_subtitles.values()
        }
        if context.source:
            for stream in context.source.external_subtitles:
                if stream.index in loaded_streams or not stream.can_stream_as_file:
                    continue
                options.append(
                    TrackOption(
                        key=f"jf:{stream.index}",
                        label=f"{stream.label}  (Jellyfin)",
                        selected=False,
                    )
                )
        self.app.push_screen(
            TrackMenuScreen("Subtitles", options),
            callback=self._subtitle_chosen,
        )

    def _subtitle_chosen(self, key: str | None) -> None:
        context = self.playback
        if not context or not key:
            return
        if key == "sid:off":
            self._player_command(self.app.player.set_subtitle, None, error_title="Subtitle failed")
            context.pending_report = True
            return
        kind, _, value = key.partition(":")
        if kind == "sid":
            self._player_command(
                self.app.player.set_subtitle, int(value), error_title="Subtitle failed"
            )
            context.pending_report = True
            return
        if kind == "jf" and context.source:
            try:
                index = int(value)
            except ValueError:
                return
            stream = next(
                (s for s in context.source.external_subtitles if s.index == index), None
            )
            if stream is None:
                return
            self._add_subtitle_stream(context, stream)

    @work(thread=True, exclusive=True, group="subtitles")
    def _add_subtitle_stream(
        self, context: PlaybackContext, stream: MediaStream
    ) -> None:
        worker = get_current_worker()
        client: JellyfinClient = self.app.client
        player: MpvPlayer = self.app.player
        try:
            url = client.subtitle_url(
                context.item.id, context.source, stream.index, stream.codec
            )
            track_id = player.add_subtitle(
                url,
                title=stream.display_title or stream.label,
                lang=stream.language,
            )
            context.external_subtitles[track_id] = stream
            context.pending_report = True
        except (MpvError, JellyfinError) as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.notify,
                    f"Could not load subtitle: {exc}",
                    severity="error",
                    timeout=6,
                    markup=False,
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(
                self.notify,
                f"Subtitle: {stream.label}",
                timeout=3,
                markup=False,
            )

    def action_toggle_watched(self) -> None:
        item = self._selected()
        if not item:
            return
        if not item.is_playable:
            self.notify("Only playable items can be marked watched.", timeout=4)
            return
        self._toggle_watched_worker(item, not item.played)

    @work(thread=True, exclusive=True, group="watched")
    def _toggle_watched_worker(self, item: Item, played: bool) -> None:
        worker = get_current_worker()
        client: JellyfinClient = self.app.client
        try:
            client.mark_played(item.id, played)
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self.notify,
                    str(exc),
                    severity="error",
                    timeout=6,
                    markup=False,
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._watched_updated, item.id, played)

    def _watched_updated(self, item_id: str, played: bool) -> None:
        item = self._items_by_id.get(item_id)
        if not item:
            return
        item.played = played
        if played:
            item.playback_position_ticks = item.runtime_ticks
            item.played_percentage = 100.0
            item.play_count += 1
        else:
            item.playback_position_ticks = 0
            item.played_percentage = 0.0
            item.play_count = 0
        table = self.query_one("#items", DataTable)
        try:
            table.update_cell(item.id, "status", item.status_text)
        except Exception:
            pass
        if self._selected_item and self._selected_item.id == item_id:
            self._show_details(item)
        self.notify(
            f"{'Marked watched' if played else 'Marked unwatched'}: {item.title}",
            timeout=3,
            markup=False,
        )

    # ---------------------------------------------------------------- search

    def action_focus_search(self) -> None:
        search = self.query_one("#search", Input)
        if not self._search_visible:
            self._pre_search_stack = list(self.stack)
            self._search_visible = True
            search.display = True
        search.focus()
        try:
            search.select_all()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        term = event.value.strip()
        if not term:
            self._close_search()
            return
        location = Location(
            key=f"search:{term}",
            title=f"Search: {term}",
            kind="search",
            search_term=term,
        )
        self.stack = [location]
        self._start_load(location)
        self.call_after_refresh(self.query_one("#items", DataTable).focus)

    def _close_search(self) -> None:
        if not self._search_visible:
            return
        search = self.query_one("#search", Input)
        search.display = False
        self._search_visible = False
        search.value = ""
        if self._pre_search_stack:
            self.stack = self._pre_search_stack
            self._pre_search_stack = []
            self._start_load(self.stack[-1])
        elif self.location:
            self._start_load(self.location)
        self.query_one("#items", DataTable).focus()

    # -------------------------------------------------------------- navigation

    def action_back(self) -> None:
        if self._search_visible:
            self._close_search()
            return
        if len(self.stack) > 1:
            self.stack.pop()
            self._start_load(self.stack[-1])
            return
        self.query_one("#libraries", OptionList).focus()

    def action_focus_items(self) -> None:
        if self.items:
            self.query_one("#items", DataTable).focus()

    def action_focus_sidebar(self) -> None:
        self.query_one("#libraries", OptionList).focus()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_toggle_images(self) -> None:
        if not self.graphics.can_show_images:
            self.notify(
                "No graphics support: use Kitty/Sixel, or install chafa for text art.",
                severity="warning",
                timeout=6,
            )
            return
        self.images_enabled = not self.images_enabled
        self.app.config.images = self.images_enabled
        self.app.save_config()
        if self.images_enabled:
            if self._selected_item:
                self._poster_size = None
                self._schedule_poster(self._selected_item)
            self.notify("Cover art on", timeout=2)
        else:
            self._hide_poster()
            self.notify("Cover art off", timeout=2)

    def action_refresh(self) -> None:
        self._load_views()
        if self.location:
            self._restore_selection_id = self._selected_item.id if self._selected_item else None
            self._start_load(self.location)

    # ------------------------------------------------------------------ exit

    def action_logout(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._shutdown_worker(logout=True)

    def action_quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._shutdown_worker(logout=False)

    @work(thread=True, exclusive=True, group="shutdown")
    def _shutdown_worker(self, *, logout: bool) -> None:
        context = self.playback
        client: JellyfinClient = self.app.client
        player: MpvPlayer = self.app.player
        audio = self.app.audio_player
        state = self._engine_state(context) if context else player.state()
        if context and self.app.config.report_progress:
            try:
                client.report_stopped(
                    context.item,
                    context.source,
                    play_session_id=context.play_session_id,
                    position_ticks=int(max(0.0, state.position) * 10_000_000),
                )
            except JellyfinError:
                pass
        player.quit()
        audio.quit()
        if logout:
            self.app.call_from_thread(self._finish_logout)
        else:
            self.app.call_from_thread(self.app.exit)

    def _finish_logout(self) -> None:
        app = self.app
        app.config.clear_session()
        app.save_config()
        app.client = None
        from .login import LoginScreen

        app.switch_screen(LoginScreen())


# --------------------------------------------------------------------- helpers


def _is_queuable(item: Item) -> bool:
    return item.type in AUDIO_TYPES or item.type in {
        "MusicAlbum",
        "MusicArtist",
    }


def _expand_music_item(client: JellyfinClient, item: Item) -> list[Item]:
    if item.type in AUDIO_TYPES:
        return [item]
    if item.type == "MusicAlbum":
        return client.get_music_tracks(
            item.id,
            recursive=False,
            sort_by="ParentIndexNumber,IndexNumber",
        )
    if item.type == "MusicArtist":
        return client.get_music_tracks(
            item.id,
            recursive=True,
            sort_by="Album,ParentIndexNumber,IndexNumber",
        )
    return []


def _row_title(item: Item) -> str:
    if item.type in AUDIO_TYPES and item.index_number is not None:
        return f"{item.index_number:02d}  {item.name}"
    return item.title


def _location_for_view(view: Item) -> Location:
    collection = view.collection_type
    include = VIEW_TYPES.get(collection, BROWSE_TYPES)
    sort_by = VIEW_SORTS.get(collection, "SortName")
    return Location(
        key=f"view:{view.id}",
        title=view.name,
        kind="library",
        parent_id=view.id,
        include_types=include,
        sort_by=sort_by,
        recursive=collection != "music",
    )


def _server_host(server: str) -> str:
    text = re.sub(r"^https?://", "", server.rstrip("/"))
    return text


def _find_track(tracks: list[Track], wanted: str) -> Track | None:
    for track in tracks:
        if _language_matches(track.lang, wanted):
            return track
    return None


def _language_matches(code: str, wanted: str) -> bool:
    if not code:
        return False
    wanted_lower = wanted.strip().lower()
    if not wanted_lower:
        return False
    code_lower = code.lower()
    if code_lower == wanted_lower or code_lower.startswith(wanted_lower):
        return True
    full = language_name(code).lower()
    return bool(full) and (full == wanted_lower or full.startswith(wanted_lower))


def _copy_item_fields(target: Item, source: Item) -> None:
    for item_field in fields(Item):
        try:
            setattr(target, item_field.name, getattr(source, item_field.name))
        except (AttributeError, TypeError):
            pass


def _report_stopped_quietly(
    client: JellyfinClient, context: PlaybackContext, state: PlayerState
) -> None:
    try:
        client.report_stopped(
            context.item,
            context.source,
            play_session_id=context.play_session_id,
            position_ticks=int(max(0.0, state.position) * 10_000_000),
        )
    except JellyfinError:
        pass
