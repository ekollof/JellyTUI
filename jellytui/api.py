"""A small, dependency-light Jellyfin API client."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urlencode

import requests

from . import __version__
from .models import AUDIO_TYPES, TICKS_PER_SECOND, Item, MediaSource

# Fields requested whenever we load a list of items. MediaSources carries the
# audio/subtitle stream metadata that the player needs for track switching.
ITEM_FIELDS = ",".join(
    [
        "Overview",
        "Genres",
        "CommunityRating",
        "OfficialRating",
        "RunTimeTicks",
        "UserData",
        "MediaSources",
        "MediaStreams",
        "ProductionYear",
        "ChildCount",
        "SeriesInfo",
        "PrimaryImageAspectRatio",
    ]
)

BROWSE_TYPES = "Movie,Series,Season,Episode,BoxSet,MusicAlbum,MusicArtist,Audio,MusicVideo,Video,Playlist,PhotoAlbum"


class JellyfinError(Exception):
    """Base class for Jellyfin API errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthenticationError(JellyfinError):
    """Raised when credentials or a token are rejected."""


class JellyfinClient:
    def __init__(
        self,
        server: str,
        token: str = "",
        user_id: str = "",
        device_id: str = "",
        *,
        client_name: str = "JellyTUI",
        version: str = __version__,
        timeout: tuple[float, float] = (6.0, 30.0),
    ) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.user_id = user_id
        self.device_id = device_id
        self.client_name = client_name
        self.version = version
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": f"{client_name}/{version}",
            }
        )

    # ------------------------------------------------------------------ HTTP

    def _auth_header(self, token: str | None = None) -> str:
        fields = [
            f'Client="{self.client_name}"',
            f'Device="{_quote_header(self.client_name)}"',
            f'DeviceId="{_quote_header(self.device_id)}"',
            f'Version="{self.version}"',
        ]
        token = self.token if token is None else token
        if token:
            fields.append(f'Token="{_quote_header(token)}"')
        return "MediaBrowser " + ", ".join(fields)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.server}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: tuple[float, float] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        headers = {
            "Authorization": self._auth_header(token),
            "X-Emby-Authorization": self._auth_header(token),
        }
        try:
            response = self.http.request(
                method,
                self._url(path),
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout or self.timeout,
                stream=stream,
            )
        except requests.exceptions.SSLError as exc:
            raise JellyfinError(
                f"TLS error while talking to {self.server}: {exc}"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise JellyfinError(
                f"Could not reach Jellyfin at {self.server}. Is the server URL correct?"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise JellyfinError(f"Jellyfin at {self.server} timed out.") from exc
        except requests.exceptions.RequestException as exc:
            raise JellyfinError(f"Request to Jellyfin failed: {exc}") from exc

        if response.status_code == 401:
            raise AuthenticationError(
                "Jellyfin rejected the credentials or session token.", 401
            )
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise JellyfinError(
                f"Jellyfin returned HTTP {response.status_code}"
                + (f": {detail}" if detail else ""),
                response.status_code,
            )
        return response

    def _get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = self._request("GET", path, params=params)
        try:
            data = response.json()
        except ValueError as exc:
            raise JellyfinError("Jellyfin returned an invalid JSON response.") from exc
        if not isinstance(data, dict):
            raise JellyfinError("Jellyfin returned an unexpected response.")
        return data

    # ------------------------------------------------------------------- auth

    def login(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate and store the resulting access token."""
        response = self._request(
            "POST",
            "/Users/AuthenticateByName",
            json_body={"Username": username, "Pw": password},
            token="",
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise JellyfinError("Jellyfin returned an invalid login response.") from exc
        token = data.get("AccessToken")
        user = data.get("User") or {}
        if not token or not user.get("Id"):
            raise AuthenticationError("Jellyfin did not return a usable session.")
        self.token = str(token)
        self.user_id = str(user["Id"])
        return user

    def get_current_user(self) -> dict[str, Any]:
        return self._get_json("/Users/Me")

    def get_public_info(self) -> dict[str, Any]:
        return self._get_json("/System/Info/Public")

    # ----------------------------------------------------------------- browse

    def get_views(self) -> list[Item]:
        data = self._get_json(
            "/UserViews",
            {
                "userId": self.user_id,
                "includeExternalContent": "false",
                "includeHidden": "false",
            },
        )
        return [Item.from_json(item) for item in data.get("Items") or []]

    def get_items(
        self,
        *,
        parent_id: str | None = None,
        include_types: str | Iterable[str] | None = None,
        recursive: bool = True,
        sort_by: str | Iterable[str] = "SortName",
        sort_order: str = "Ascending",
        search_term: str | None = None,
        limit: int | None = 200,
        media_types: str | None = None,
    ) -> list[Item]:
        params: dict[str, Any] = {
            "userId": self.user_id,
            "recursive": "true" if recursive else "false",
            "fields": ITEM_FIELDS,
            "enableImages": "true",
            "enableImageTypes": "Primary",
            "imageTypeLimit": 1,
            "enableUserData": "true",
            "sortBy": _join(sort_by),
            "sortOrder": sort_order,
        }
        if parent_id:
            params["parentId"] = parent_id
        if include_types:
            params["includeItemTypes"] = _join(include_types)
        if search_term:
            params["searchTerm"] = search_term
        if media_types:
            params["mediaTypes"] = media_types
        if limit:
            params["limit"] = limit
        data = self._get_json("/Items", params)
        return [Item.from_json(item) for item in data.get("Items") or []]

    def get_resume(self, limit: int = 30) -> list[Item]:
        data = self._get_json(
            "/UserItems/Resume",
            {
                "userId": self.user_id,
                "limit": limit,
                "mediaTypes": "Video,Audio",
                "fields": ITEM_FIELDS,
                "enableImages": "true",
                "enableImageTypes": "Primary",
                "imageTypeLimit": 1,
                "enableUserData": "true",
            },
        )
        return [Item.from_json(item) for item in data.get("Items") or []]

    def get_latest(
        self,
        parent_id: str | None = None,
        limit: int = 30,
        include_types: str | None = None,
    ) -> list[Item]:
        params: dict[str, Any] = {
            "userId": self.user_id,
            "limit": limit,
            "fields": ITEM_FIELDS,
            "enableImages": "true",
            "enableImageTypes": "Primary",
            "imageTypeLimit": 1,
            "enableUserData": "true",
        }
        if parent_id:
            params["parentId"] = parent_id
        if include_types:
            params["includeItemTypes"] = include_types
        response = self._request("GET", "/Items/Latest", params=params)
        try:
            data = response.json()
        except ValueError:
            return []
        # The Latest endpoint returns a bare list on older servers.
        items = data.get("Items") if isinstance(data, dict) else data
        return [Item.from_json(item) for item in items or [] if isinstance(item, dict)]

    def get_item(self, item_id: str, *, media: bool = True) -> Item:
        params: dict[str, Any] = {"userId": self.user_id}
        if media:
            params["fields"] = ITEM_FIELDS
        data = self._get_json(f"/Items/{quote(item_id)}", params)
        return Item.from_json(data)

    def get_playback_info(self, item_id: str) -> MediaSource | None:
        """Ask the server for a fresh media source (used when list data is stale)."""
        data = self._get_json(
            f"/Items/{quote(item_id)}/PlaybackInfo", {"userId": self.user_id}
        )
        sources = data.get("MediaSources") or []
        if not sources:
            return None
        return MediaSource.from_json(sources[0])

    # ------------------------------------------------------------------ user

    def mark_played(self, item_id: str, played: bool = True) -> None:
        method = "POST" if played else "DELETE"
        self._request(
            method,
            f"/UserPlayedItems/{quote(item_id)}",
            params={"userId": self.user_id},
        )

    def report_start(
        self,
        item: Item,
        source: MediaSource | None,
        *,
        play_session_id: str,
        position_ticks: int = 0,
        audio_index: int | None = None,
        subtitle_index: int | None = None,
        paused: bool = False,
    ) -> None:
        self._request(
            "POST",
            "/Sessions/Playing",
            json_body=_progress_body(
                item,
                source,
                play_session_id=play_session_id,
                position_ticks=position_ticks,
                audio_index=audio_index,
                subtitle_index=subtitle_index,
                paused=paused,
            ),
        )

    def report_progress(
        self,
        item: Item,
        source: MediaSource | None,
        *,
        play_session_id: str,
        position_ticks: int,
        audio_index: int | None = None,
        subtitle_index: int | None = None,
        paused: bool = False,
        volume: int | None = None,
    ) -> None:
        body = _progress_body(
            item,
            source,
            play_session_id=play_session_id,
            position_ticks=position_ticks,
            audio_index=audio_index,
            subtitle_index=subtitle_index,
            paused=paused,
        )
        if volume is not None:
            body["VolumeLevel"] = max(0, min(100, int(volume)))
        self._request("POST", "/Sessions/Playing/Progress", json_body=body)

    def report_stopped(
        self,
        item: Item,
        source: MediaSource | None,
        *,
        play_session_id: str,
        position_ticks: int,
        audio_index: int | None = None,
        subtitle_index: int | None = None,
    ) -> None:
        self._request(
            "POST",
            "/Sessions/Playing/Stopped",
            json_body=_progress_body(
                item,
                source,
                play_session_id=play_session_id,
                position_ticks=position_ticks,
                audio_index=audio_index,
                subtitle_index=subtitle_index,
                paused=False,
            ),
        )

    # ------------------------------------------------------------------ urls

    def stream_url(
        self,
        item: Item,
        source: MediaSource | None,
        *,
        play_session_id: str | None = None,
    ) -> str:
        """Build a direct-play URL suitable for mpv."""
        route = "/Audio" if item.type in AUDIO_TYPES else "/Videos"
        params: dict[str, Any] = {
            "static": "true",
            "api_key": self.token,
            "deviceId": self.device_id,
        }
        if source and source.id:
            params["mediaSourceId"] = source.id
        if play_session_id:
            params["playSessionId"] = play_session_id
        return f"{self.server}{route}/{quote(item.id)}/stream?{urlencode(params)}"

    def subtitle_url(
        self, item_id: str, source: MediaSource | None, stream_index: int, codec: str
    ) -> str:
        media_source_id = source.id if source and source.id else item_id
        fmt = subtitle_format(codec)
        path = (
            f"/Videos/{quote(item_id)}/{quote(media_source_id)}"
            f"/Subtitles/{stream_index}/Stream.{fmt}"
        )
        return f"{self.server}{path}?{urlencode({'api_key': self.token})}"

    def image_url(self, item_id: str, *, max_height: int = 600) -> str:
        params = urlencode({"maxHeight": max_height, "api_key": self.token})
        return (
            f"{self.server}/Items/{quote(item_id)}/Images/Primary?{params}"
        )

    def fetch_image(
        self, item_id: str, *, tag: str = "", max_height: int = 600
    ) -> tuple[bytes, str] | None:
        """Fetch primary artwork. Returns (bytes, content type) or None when absent."""
        params: dict[str, Any] = {"maxHeight": max_height}
        if tag:
            params["tag"] = tag
        try:
            response = self._request(
                "GET",
                f"/Items/{quote(item_id)}/Images/Primary",
                params=params,
                timeout=(5.0, 20.0),
            )
        except JellyfinError as exc:
            if exc.status == 404:
                return None
            raise
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        return response.content, content_type


# ----------------------------------------------------------------- helpers


def _quote_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _join(value: str | Iterable[str]) -> str:
    if isinstance(value, str):
        return value
    return ",".join(value)


def _error_detail(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:300]
    if isinstance(data, dict):
        for key in ("Message", "message", "error", "title"):
            if data.get(key):
                return str(data[key])[:300]
    return ""


def _progress_body(
    item: Item,
    source: MediaSource | None,
    *,
    play_session_id: str,
    position_ticks: int,
    audio_index: int | None,
    subtitle_index: int | None,
    paused: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ItemId": item.id,
        "PlaySessionId": play_session_id,
        "PositionTicks": max(0, int(position_ticks)),
        "IsPaused": bool(paused),
        "IsMuted": False,
        "CanSeek": True,
        "PlayMethod": "DirectPlay",
    }
    if source and source.id:
        body["MediaSourceId"] = source.id
    if audio_index is not None:
        body["AudioStreamIndex"] = int(audio_index)
    if subtitle_index is not None:
        body["SubtitleStreamIndex"] = int(subtitle_index)
    return body


SUBTITLE_FORMATS = {
    "subrip": "srt",
    "srt": "srt",
    "ass": "ass",
    "ssa": "ass",
    "webvtt": "vtt",
    "vtt": "vtt",
    "mov_text": "srt",
    "ttml": "ttml",
    "subviewer": "sub",
    "microdvd": "sub",
    "sami": "smi",
    "vobsub": "sub",
    "pgssub": "sup",
    "dvdsub": "sub",
    "sup": "sup",
}


def subtitle_format(codec: str) -> str:
    return SUBTITLE_FORMATS.get((codec or "").lower(), "srt")


def position_ticks(seconds: float | None) -> int:
    if not seconds or seconds <= 0:
        return 0
    return int(seconds * TICKS_PER_SECOND)
