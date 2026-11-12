"""Data models and formatting helpers for JellyTUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TICKS_PER_SECOND = 10_000_000

PLAYABLE_TYPES = {
    "Movie",
    "Episode",
    "Audio",
    "AudioBook",
    "MusicVideo",
    "Video",
    "Trailer",
    "Recording",
}

BROWSABLE_TYPES = {
    "Series",
    "Season",
    "MusicAlbum",
    "MusicArtist",
    "BoxSet",
    "CollectionFolder",
    "UserView",
    "Folder",
    "Playlist",
    "PhotoAlbum",
    "AggregateFolder",
}

AUDIO_TYPES = {"Audio", "AudioBook"}

LANGUAGE_NAMES: dict[str, str] = {
    "ara": "Arabic",
    "bul": "Bulgarian",
    "cat": "Catalan",
    "chi": "Chinese",
    "zho": "Chinese",
    "hrv": "Croatian",
    "cze": "Czech",
    "ces": "Czech",
    "dan": "Danish",
    "dut": "Dutch",
    "nld": "Dutch",
    "eng": "English",
    "est": "Estonian",
    "fin": "Finnish",
    "fre": "French",
    "fra": "French",
    "ger": "German",
    "deu": "German",
    "gre": "Greek",
    "ell": "Greek",
    "heb": "Hebrew",
    "hin": "Hindi",
    "hun": "Hungarian",
    "ice": "Icelandic",
    "isl": "Icelandic",
    "ind": "Indonesian",
    "ita": "Italian",
    "jpn": "Japanese",
    "kor": "Korean",
    "lav": "Latvian",
    "lit": "Lithuanian",
    "nor": "Norwegian",
    "pol": "Polish",
    "por": "Portuguese",
    "rum": "Romanian",
    "ron": "Romanian",
    "rus": "Russian",
    "srp": "Serbian",
    "slo": "Slovak",
    "slk": "Slovak",
    "slv": "Slovenian",
    "spa": "Spanish",
    "swe": "Swedish",
    "tha": "Thai",
    "tur": "Turkish",
    "ukr": "Ukrainian",
    "vie": "Vietnamese",
    "afr": "Afrikaans",
    "baq": "Basque",
    "eus": "Basque",
    "fil": "Filipino",
    "gle": "Irish",
    "glg": "Galician",
    "mal": "Malayalam",
    "may": "Malay",
    "msa": "Malay",
    "per": "Persian",
    "fas": "Persian",
    "tam": "Tamil",
    "tel": "Telugu",
    "urd": "Urdu",
    "wel": "Welsh",
    "cym": "Welsh",
}

# Convenience aliases for the common 2-letter codes used in subtitle files.
for _two, _three in {
    "ar": "ara",
    "bg": "bul",
    "ca": "cat",
    "cs": "cze",
    "da": "dan",
    "de": "ger",
    "el": "gre",
    "en": "eng",
    "es": "spa",
    "et": "est",
    "fa": "per",
    "fi": "fin",
    "fr": "fre",
    "he": "heb",
    "hi": "hin",
    "hr": "hrv",
    "hu": "hun",
    "id": "ind",
    "is": "ice",
    "it": "ita",
    "ja": "jpn",
    "ko": "kor",
    "lt": "lit",
    "lv": "lav",
    "ms": "may",
    "nl": "dut",
    "no": "nor",
    "pl": "pol",
    "pt": "por",
    "ro": "rum",
    "ru": "rus",
    "sk": "slo",
    "sl": "slv",
    "sr": "srp",
    "sv": "swe",
    "th": "tha",
    "tr": "tur",
    "uk": "ukr",
    "vi": "vie",
    "zh": "chi",
}.items():
    LANGUAGE_NAMES.setdefault(_two, LANGUAGE_NAMES[_three])


def language_name(code: str | None) -> str:
    if not code:
        return ""
    return LANGUAGE_NAMES.get(code.lower(), code.upper())


def format_ticks(ticks: float | None) -> str:
    """Format Jellyfin runtime ticks as a compact duration."""
    if not ticks:
        return ""
    seconds = int(ticks / TICKS_PER_SECOND)
    return format_duration(seconds)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def format_clock(seconds: float | None) -> str:
    """Format seconds as h:mm:ss or m:ss."""
    if seconds is None or seconds < 0:
        seconds = 0
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_ticks_clock(ticks: float | None) -> str:
    if not ticks:
        return "0:00"
    return format_clock(ticks / TICKS_PER_SECOND)


def format_rating(value: float | None) -> str:
    if not value:
        return ""
    return f"{value:.1f}"


@dataclass(slots=True)
class RemoteSearchResult:
    name: str
    year: int | None = None
    provider_name: str = ""
    provider_ids: dict[str, str] = field(default_factory=dict)
    overview: str = ""
    album_artist: str = ""
    artists: list[str] = field(default_factory=list)
    image_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RemoteSearchResult:
        album_artist = data.get("AlbumArtist")
        if isinstance(album_artist, dict):
            album_artist = album_artist.get("Name") or ""
        artists = []
        for artist in data.get("Artists") or []:
            if isinstance(artist, dict):
                name = artist.get("Name")
                if name:
                    artists.append(str(name))
            elif artist:
                artists.append(str(artist))
        return cls(
            name=str(data.get("Name") or "Unknown"),
            year=_optional_int(data.get("ProductionYear")),
            provider_name=str(data.get("SearchProviderName") or ""),
            provider_ids={
                str(key): str(value)
                for key, value in (data.get("ProviderIds") or {}).items()
            },
            overview=str(data.get("Overview") or ""),
            album_artist=str(album_artist or ""),
            artists=artists,
            image_url=str(data.get("ImageUrl") or ""),
            raw=dict(data),
        )

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.year:
            parts.append(f"({self.year})")
        if self.provider_name:
            parts.append(f"· {self.provider_name}")
        return "  ".join(parts)


@dataclass(slots=True)
class MediaStream:
    index: int
    type: str
    codec: str = ""
    language: str = ""
    display_title: str = ""
    title: str = ""
    is_external: bool = False
    is_text_subtitle: bool = False
    supports_external_stream: bool = False
    is_default: bool = False
    is_forced: bool = False
    is_hearing_impaired: bool = False
    channels: int = 0
    channel_layout: str = ""
    delivery_method: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MediaStream:
        return cls(
            index=int(data.get("Index") or 0),
            type=str(data.get("Type") or ""),
            codec=str(data.get("Codec") or ""),
            language=str(data.get("Language") or ""),
            display_title=str(data.get("DisplayTitle") or ""),
            title=str(data.get("Title") or ""),
            is_external=bool(data.get("IsExternal")),
            is_text_subtitle=bool(data.get("IsTextSubtitleStream")),
            supports_external_stream=bool(data.get("SupportsExternalStream")),
            is_default=bool(data.get("IsDefault")),
            is_forced=bool(data.get("IsForced")),
            is_hearing_impaired=bool(data.get("IsHearingImpaired")),
            channels=int(data.get("Channels") or 0),
            channel_layout=str(data.get("ChannelLayout") or ""),
            delivery_method=str(data.get("DeliveryMethod") or ""),
        )

    @property
    def language_label(self) -> str:
        return language_name(self.language)

    @property
    def label(self) -> str:
        if self.display_title:
            return self.display_title
        bits = [self.language_label or "Unknown"]
        if self.title and self.title.lower() not in bits[0].lower():
            bits.append(self.title)
        if self.codec:
            bits.append(self.codec.upper())
        return " · ".join(bits)

    @property
    def can_stream_as_file(self) -> bool:
        """Whether Jellyfin can serve this subtitle through its subtitle endpoint."""
        return self.type == "Subtitle" and (self.supports_external_stream or self.is_text_subtitle)

    @property
    def short_label(self) -> str:
        parts = []
        if self.language_label:
            parts.append(self.language_label)
        if self.is_forced:
            parts.append("forced")
        elif self.is_hearing_impaired:
            parts.append("SDH")
        if not parts:
            parts.append(self.label)
        return " · ".join(parts)


@dataclass(slots=True)
class MediaSource:
    id: str = ""
    name: str = ""
    container: str = ""
    runtime_ticks: int = 0
    supports_direct_play: bool = False
    supports_direct_stream: bool = False
    default_audio_index: int | None = None
    default_subtitle_index: int | None = None
    streams: list[MediaStream] = field(default_factory=list)
    size: int = 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MediaSource:
        return cls(
            id=str(data.get("Id") or ""),
            name=str(data.get("Name") or ""),
            container=str(data.get("Container") or ""),
            runtime_ticks=int(data.get("RunTimeTicks") or 0),
            supports_direct_play=bool(data.get("SupportsDirectPlay")),
            supports_direct_stream=bool(data.get("SupportsDirectStream")),
            default_audio_index=_optional_int(data.get("DefaultAudioStreamIndex")),
            default_subtitle_index=_optional_int(data.get("DefaultSubtitleStreamIndex")),
            streams=[MediaStream.from_json(s) for s in data.get("MediaStreams") or []],
            size=int(data.get("Size") or 0),
        )

    @property
    def audio_streams(self) -> list[MediaStream]:
        return sorted(
            (s for s in self.streams if s.type == "Audio"),
            key=lambda s: s.index,
        )

    @property
    def subtitle_streams(self) -> list[MediaStream]:
        return sorted(
            (s for s in self.streams if s.type == "Subtitle"),
            key=lambda s: s.index,
        )

    @property
    def video_stream(self) -> MediaStream | None:
        return next((s for s in self.streams if s.type == "Video"), None)

    @property
    def embedded_subtitles(self) -> list[MediaStream]:
        return [s for s in self.subtitle_streams if not s.is_external]

    @property
    def external_subtitles(self) -> list[MediaStream]:
        return [s for s in self.subtitle_streams if s.is_external]

    def stream_by_index(self, index: int | None) -> MediaStream | None:
        if index is None:
            return None
        return next((s for s in self.streams if s.index == index), None)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class Item:
    id: str
    name: str
    type: str
    series_id: str = ""
    series_name: str = ""
    season_name: str = ""
    index_number: int | None = None
    parent_index_number: int | None = None
    production_year: int | None = None
    runtime_ticks: int = 0
    overview: str = ""
    genres: list[str] = field(default_factory=list)
    community_rating: float | None = None
    official_rating: str = ""
    played: bool = False
    playback_position_ticks: int = 0
    played_percentage: float = 0.0
    play_count: int = 0
    media_sources: list[MediaSource] = field(default_factory=list)
    child_count: int | None = None
    album: str = ""
    album_id: str = ""
    album_image_tag: str = ""
    artists: list[str] = field(default_factory=list)
    album_artists: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    original_title: str = ""
    sort_name: str = ""
    forced_sort_name: str = ""
    premiere_date: str = ""
    collection_type: str = ""
    image_tag: str = ""
    series_image_tag: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Item:
        user_data = data.get("UserData") or {}
        return cls(
            id=str(data.get("Id") or ""),
            name=str(data.get("Name") or "Untitled"),
            type=str(data.get("Type") or ""),
            series_id=str(data.get("SeriesId") or ""),
            series_name=str(data.get("SeriesName") or ""),
            season_name=str(data.get("SeasonName") or ""),
            index_number=_optional_int(data.get("IndexNumber")),
            parent_index_number=_optional_int(data.get("ParentIndexNumber")),
            production_year=_optional_int(data.get("ProductionYear")),
            runtime_ticks=int(data.get("RunTimeTicks") or 0),
            overview=str(data.get("Overview") or ""),
            genres=[str(g) for g in data.get("Genres") or []],
            community_rating=_optional_float(data.get("CommunityRating")),
            official_rating=str(data.get("OfficialRating") or ""),
            played=bool(user_data.get("Played")),
            playback_position_ticks=int(user_data.get("PlaybackPositionTicks") or 0),
            played_percentage=float(user_data.get("PlayedPercentage") or 0.0),
            play_count=int(user_data.get("PlayCount") or 0),
            media_sources=[
                MediaSource.from_json(ms) for ms in data.get("MediaSources") or []
            ],
            child_count=_optional_int(data.get("ChildCount")),
            album=str(data.get("Album") or ""),
            album_id=str(data.get("AlbumId") or ""),
            album_image_tag=str(data.get("AlbumPrimaryImageTag") or ""),
            artists=[str(a) for a in data.get("Artists") or []],
            album_artists=[
                str(artist.get("Name") or "")
                for artist in data.get("AlbumArtists") or []
                if isinstance(artist, dict)
            ],
            tags=[str(tag) for tag in data.get("Tags") or []],
            original_title=str(data.get("OriginalTitle") or ""),
            sort_name=str(data.get("SortName") or ""),
            forced_sort_name=str(data.get("ForcedSortName") or ""),
            premiere_date=str(data.get("PremiereDate") or ""),
            collection_type=str(data.get("CollectionType") or ""),
            image_tag=str((data.get("ImageTags") or {}).get("Primary") or ""),
            series_image_tag=str(data.get("SeriesPrimaryImageTag") or ""),
        )

    @property
    def is_playable(self) -> bool:
        if self.type in PLAYABLE_TYPES:
            return True
        return bool(self.media_sources) and self.type not in BROWSABLE_TYPES

    @property
    def is_browsable(self) -> bool:
        return self.type in BROWSABLE_TYPES

    @property
    def media_source(self) -> MediaSource | None:
        """Pick the best media source for direct playback."""
        if not self.media_sources:
            return None
        direct = [ms for ms in self.media_sources if ms.supports_direct_play]
        return (direct or self.media_sources)[0]

    @property
    def episode_code(self) -> str:
        if self.type != "Episode":
            return ""
        season = self.parent_index_number
        episode = self.index_number
        if season is None and episode is None:
            return ""
        season_text = f"{season:02d}" if season is not None else "??"
        episode_text = f"{episode:02d}" if episode is not None else "??"
        return f"S{season_text}E{episode_text}"

    @property
    def title(self) -> str:
        if self.episode_code:
            return f"{self.episode_code} · {self.name}"
        if self.type in AUDIO_TYPES and self.album:
            return self.name
        return self.name

    @property
    def detail_subtitle(self) -> str:
        parts: list[str] = []
        if self.type == "Episode" and self.series_name:
            series = self.series_name
            if self.season_name:
                series += f" · {self.season_name}"
            parts.append(series)
        elif self.type in AUDIO_TYPES:
            artist = ", ".join(self.artists)
            if artist and self.album:
                parts.append(f"{artist} — {self.album}")
            elif artist:
                parts.append(artist)
            elif self.album:
                parts.append(self.album)
        elif self.type == "MusicAlbum":
            artist = ", ".join(self.album_artists or self.artists)
            if artist:
                parts.append(artist)
        elif self.series_name:
            parts.append(self.series_name)
        if self.production_year:
            parts.append(str(self.production_year))
        runtime = format_ticks(self.runtime_ticks)
        if runtime:
            parts.append(runtime)
        if self.community_rating:
            parts.append(f"★ {self.community_rating:.1f}")
        if self.official_rating:
            parts.append(self.official_rating)
        return "  ·  ".join(parts)

    @property
    def progress_percent(self) -> float:
        """Progress in 0..100, preferring the playback position over the stored percentage."""
        if self.played:
            return 100.0
        if self.runtime_ticks and self.playback_position_ticks:
            return min(100.0, 100.0 * self.playback_position_ticks / self.runtime_ticks)
        return min(100.0, float(self.played_percentage or 0.0))

    @property
    def status_text(self) -> str:
        if self.child_count:
            if self.type == "Series":
                child = f"{self.child_count} season{'s' if self.child_count != 1 else ''}"
            elif self.type == "Season":
                child = f"{self.child_count} episode{'s' if self.child_count != 1 else ''}"
            elif self.type == "MusicAlbum":
                child = f"{self.child_count} track{'s' if self.child_count != 1 else ''}"
            else:
                child = f"{self.child_count} item{'s' if self.child_count != 1 else ''}"
            if self.is_browsable:
                return child
        else:
            child = ""
        if self.played:
            return "✓"
        percent = self.progress_percent
        if percent >= 0.5:
            return f"{percent:.0f}%"
        return child

    @property
    def resume_seconds(self) -> float:
        return self.playback_position_ticks / TICKS_PER_SECOND

    @property
    def artwork(self) -> tuple[str, str] | None:
        """Return (item id, image tag) for the best available artwork."""
        if self.image_tag:
            return self.id, self.image_tag
        if self.type in AUDIO_TYPES and self.album_id and self.album_image_tag:
            return self.album_id, self.album_image_tag
        if self.type == "Episode" and self.series_id and self.series_image_tag:
            return self.series_id, self.series_image_tag
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
