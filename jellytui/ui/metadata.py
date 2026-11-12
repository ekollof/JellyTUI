"""Metadata editor for Jellyfin items."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea
from textual.worker import get_current_worker

from ..api import JellyfinError
from ..models import AUDIO_TYPES, Item


class MetadataScreen(ModalScreen[Item | bool | None]):
    """Edit the common metadata fields of a Jellyfin item."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, item: Item) -> None:
        super().__init__()
        self.item = item
        self._busy = False
        self._raw: dict = {}
        self.is_audio = item.type in AUDIO_TYPES
        self.is_music_album = item.type == "MusicAlbum"
        self.is_music_artist = item.type == "MusicArtist"
        self.show_album_artists = self.is_audio or self.is_music_album
        self.lookup_type = {
            "Movie": "Movie",
            "Series": "Series",
            "BoxSet": "BoxSet",
            "MusicVideo": "MusicVideo",
        }.get(item.type)

    def compose(self) -> ComposeResult:
        item = self.item
        with Vertical(id="metadata-panel"):
            yield Static("Edit metadata", id="dialog-title")
            yield Static(Text(item.title), classes="dialog-subject")
            if self.is_audio or self.is_music_album or self.is_music_artist or self.lookup_type:
                with Horizontal(id="metadata-lookup"):
                    if self.is_audio or self.is_music_album:
                        yield Button("Look up album…", id="lookup-album")
                    if self.is_audio or self.is_music_album or self.is_music_artist:
                        yield Button("Look up artist…", id="lookup-artist")
                    if self.lookup_type:
                        yield Button("Look up metadata…", id="lookup-item")
            with VerticalScroll(id="metadata-scroll"):
                yield Label("Title", classes="field-label")
                yield Input(value=item.name, id="meta-name")
                yield Label("Sort title", classes="field-label")
                yield Input(value=item.forced_sort_name, id="meta-sort")
                yield Label("Original title", classes="field-label")
                yield Input(value=item.original_title, id="meta-original")
                yield Label("Overview", classes="field-label")
                yield TextArea(item.overview or "", id="meta-overview")
                with Horizontal(classes="metadata-row"):
                    with Vertical():
                        yield Label("Year", classes="field-label")
                        yield Input(
                            value=str(item.production_year or ""),
                            id="meta-year",
                        )
                    with Vertical():
                        yield Label("Community rating", classes="field-label")
                        yield Input(
                            value=(
                                f"{item.community_rating:g}"
                                if item.community_rating
                                else ""
                            ),
                            id="meta-community",
                        )
                    with Vertical():
                        yield Label("Official rating", classes="field-label")
                        yield Input(
                            value=item.official_rating,
                            id="meta-official",
                        )
                yield Label("Genres (semicolon separated)", classes="field-label")
                yield Input(value="; ".join(item.genres), id="meta-genres")
                yield Label("Tags (semicolon separated)", classes="field-label")
                yield Input(value="; ".join(item.tags), id="meta-tags")
                if self.is_audio:
                    yield Static("MUSIC", classes="metadata-section")
                    yield Label("Album", classes="field-label")
                    yield Input(value=item.album, id="meta-album")
                    yield Label("Artists (semicolon separated)", classes="field-label")
                    yield Input(value="; ".join(item.artists), id="meta-artists")
                elif self.is_music_album:
                    yield Static("MUSIC", classes="metadata-section")
                if self.show_album_artists:
                    yield Label("Album artists (semicolon separated)", classes="field-label")
                    yield Input(
                        value="; ".join(item.album_artists),
                        id="meta-album-artists",
                    )
                if self.is_audio:
                    with Horizontal(classes="metadata-row"):
                        with Vertical():
                            yield Label("Track number", classes="field-label")
                            yield Input(
                                value=str(item.index_number or ""),
                                id="meta-track",
                            )
                        with Vertical():
                            yield Label("Disc number", classes="field-label")
                            yield Input(
                                value=str(item.parent_index_number or ""),
                                id="meta-disc",
                            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")
            yield Static("", id="metadata-status")

    def on_mount(self) -> None:
        self.query_one("#meta-name", Input).focus()
        self._load_edit_data()

    @work(thread=True, exclusive=True, group="metadata-load")
    def _load_edit_data(self) -> None:
        worker = get_current_worker()
        client = self.app.client
        try:
            raw = client.get_item_edit_data(self.item.id)
        except Exception:
            return
        if not worker.is_cancelled and self.is_mounted:
            self.app.call_from_thread(self._apply_raw, raw)

    def _apply_raw(self, raw: dict) -> None:
        if not self.is_mounted or self._busy:
            return
        self._raw = raw
        self._set_input("#meta-name", raw.get("Name"))
        self._set_input("#meta-sort", raw.get("ForcedSortName") or "")
        self._set_input("#meta-original", raw.get("OriginalTitle"))
        overview = raw.get("Overview")
        if overview is not None:
            self.query_one("#meta-overview", TextArea).text = str(overview)
        self._set_input("#meta-year", raw.get("ProductionYear"))
        self._set_input("#meta-community", raw.get("CommunityRating"))
        self._set_input("#meta-official", raw.get("OfficialRating"))
        self._set_input(
            "#meta-genres",
            "; ".join(str(value) for value in raw.get("Genres") or []),
        )
        self._set_input(
            "#meta-tags",
            "; ".join(str(value) for value in raw.get("Tags") or []),
        )
        if not self.is_audio:
            return
        self._set_input("#meta-album", raw.get("Album"))
        artists = raw.get("ArtistItems")
        if isinstance(artists, list):
            artist_names = [
                str(entry.get("Name") or "")
                for entry in artists
                if isinstance(entry, dict)
            ]
        else:
            artist_names = [str(value) for value in raw.get("Artists") or []]
        self._set_input("#meta-artists", "; ".join(artist_names))
        album_artists = raw.get("AlbumArtists")
        if isinstance(album_artists, list):
            album_artist_names = [
                str(entry.get("Name") or "")
                for entry in album_artists
                if isinstance(entry, dict)
            ]
        else:
            album_artist_names = []
        self._set_input("#meta-album-artists", "; ".join(album_artist_names))
        self._set_input("#meta-track", raw.get("IndexNumber"))
        self._set_input("#meta-disc", raw.get("ParentIndexNumber"))

    def _set_input(self, selector: str, value: object) -> None:
        if value is None:
            value = ""
        self.query_one(selector, Input).value = str(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "lookup-album":
            self._lookup_album()
        elif event.button.id == "lookup-artist":
            self._lookup_artist()
        elif event.button.id == "lookup-item":
            self._lookup_item()

    def action_cancel(self) -> None:
        if not self._busy:
            self.dismiss(None)

    def action_save(self) -> None:
        if self._busy:
            return
        changes = self._changes()
        if changes is None:
            return
        self._set_busy(True)
        self._set_status("Saving metadata…")
        self._save_worker(changes)

    def _changes(self) -> dict | None:
        name = self.query_one("#meta-name", Input).value.strip()
        if not name:
            self._set_status("Title cannot be empty.", error=True)
            self.query_one("#meta-name", Input).focus()
            return None
        try:
            year = self._optional_int("#meta-year", "Year")
            community = self._optional_float("#meta-community", "Community rating")
            if community is not None and not 0 <= community <= 10:
                raise ValueError("Community rating must be between 0 and 10.")
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return None

        changes: dict[str, object] = {
            "Name": name,
            "ForcedSortName": self._text("#meta-sort") or None,
            "OriginalTitle": self._text("#meta-original") or None,
            "Overview": self._overview() or None,
            "ProductionYear": year,
            "CommunityRating": community,
            "OfficialRating": self._text("#meta-official") or None,
            "Genres": _split_list(self._text("#meta-genres")),
            "Tags": _split_list(self._text("#meta-tags")),
        }
        if self.is_audio:
            try:
                track = self._optional_int("#meta-track", "Track number")
                disc = self._optional_int("#meta-disc", "Disc number")
            except ValueError as exc:
                self._set_status(str(exc), error=True)
                return None
            changes.update(
                {
                    "Album": self._text("#meta-album") or None,
                    "ArtistItems": [
                        {"Name": artist}
                        for artist in _split_list(self._text("#meta-artists"))
                    ],
                    "IndexNumber": track,
                    "ParentIndexNumber": disc,
                }
            )
        if self.show_album_artists:
            changes["AlbumArtists"] = [
                {"Name": artist}
                for artist in _split_list(self._text("#meta-album-artists"))
            ]
        return changes

    def _text(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _overview(self) -> str:
        return self.query_one("#meta-overview", TextArea).text.strip()

    def _optional_int(self, selector: str, label: str) -> int | None:
        value = self._text(selector)
        if not value:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a whole number.") from exc

    def _optional_float(self, selector: str, label: str) -> float | None:
        value = self._text(selector)
        if not value:
            return None
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc

    @work(thread=True, exclusive=True, group="metadata")
    def _save_worker(self, changes: dict) -> None:
        worker = get_current_worker()
        client = self.app.client
        try:
            updated = client.update_item_metadata(self.item.id, changes)
        except JellyfinError as exc:
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(self._save_failed, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(
                    self._save_failed, f"Unexpected error: {exc}"
                )
            return
        if not worker.is_cancelled and self.is_mounted:
            self.app.call_from_thread(self.dismiss, updated)

    def _save_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message, error=True)

    # ---------------------------------------------------------------- lookup

    def _lookup_album(self) -> None:
        if self.is_music_album:
            target_id = self.item.id
            name = self.item.name
            year = self.item.production_year
            description = f"Music album: {name}"
        elif self.is_audio:
            if not self.item.album_id:
                self._set_status(
                    "This track has no linked album to look up.", error=True
                )
                return
            target_id = self.item.album_id
            name = self.item.album or self.item.name
            year = self.item.production_year
            description = f"Music album: {name}"
        else:
            return
        self._open_lookup(
            target_id,
            "MusicAlbum",
            name,
            year,
            description,
        )

    def _lookup_artist(self) -> None:
        if self.is_music_artist:
            target_id = self.item.id
            name = self.item.name
            description = f"Music artist: {name}"
        else:
            target = self._artist_reference()
            if target is None:
                self._set_status(
                    "No linked artist item was found. Open the artist page and "
                    "edit it there.",
                    error=True,
                )
                return
            target_id, name = target
            description = f"Music artist: {name}"
        self._open_lookup(
            target_id,
            "MusicArtist",
            name,
            None,
            description,
        )

    def _lookup_item(self) -> None:
        if not self.lookup_type:
            return
        self._open_lookup(
            self.item.id,
            self.lookup_type,
            self.item.name,
            self.item.production_year,
            f"{self.lookup_type}: {self.item.name}",
        )

    def _artist_reference(self) -> tuple[str, str] | None:
        if not self._raw:
            self._set_status("Metadata is still loading; try again.", error=True)
            return None
        keys = ("ArtistItems", "AlbumArtists") if self.is_audio else ("AlbumArtists",)
        for key in keys:
            for entry in self._raw.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                artist_id = entry.get("Id")
                artist_name = entry.get("Name")
                if artist_id and artist_name:
                    return str(artist_id), str(artist_name)
        return None

    def _open_lookup(
        self,
        target_item_id: str,
        search_type: str,
        name: str,
        year: int | None,
        description: str,
    ) -> None:
        if self.app.client is None:
            return
        from .lookup import LookupScreen

        self.app.push_screen(
            LookupScreen(
                self.app.client,
                target_item_id=target_item_id,
                search_type=search_type,
                name=name,
                year=year,
                description=description,
            ),
            callback=self._lookup_finished,
        )

    def _lookup_finished(self, applied: bool | None) -> None:
        if applied:
            self.dismiss(True)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in self.query(Input):
            widget.disabled = busy
        for widget in self.query(TextArea):
            widget.disabled = busy
        self.query_one("#save", Button).disabled = busy
        self.query_one("#cancel", Button).disabled = busy

    def _set_status(self, message: str, *, error: bool = False) -> None:
        style = f"bold {self.app.current_theme.error}" if error else "dim"
        self.query_one("#metadata-status", Static).update(Text(message, style=style))


def _split_list(value: str) -> list[str]:
    seen: list[str] = []
    for part in value.split(";"):
        item = part.strip()
        if item and item not in seen:
            seen.append(item)
    return seen
