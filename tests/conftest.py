"""Shared fixtures: a small fake Jellyfin server."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from jellytui.models import TICKS_PER_SECOND


def _media_stream(index, type_, codec="", language="", title="", external=False,
                  channels=None, text_subtitle=False, default=False) -> dict:
    data = {
        "Index": index,
        "Type": type_,
        "Codec": codec,
        "Language": language or None,
        "Title": title,
        "DisplayTitle": title or codec.upper(),
        "IsExternal": external,
        "IsTextSubtitleStream": text_subtitle,
        "SupportsExternalStream": text_subtitle or external,
        "IsDefault": default,
        "IsForced": False,
    }
    if channels is not None:
        data["Channels"] = channels
        data["ChannelLayout"] = "5.1" if channels == 6 else "stereo"
    return data


def fake_media_source(
    item_id: str, *, container: str = "mkv", runtime_ticks: int = 81600000000
) -> dict:
    return {
        "Id": item_id,
        "Name": "Fake source",
        "Container": container,
        "RunTimeTicks": runtime_ticks,
        "SupportsDirectPlay": True,
        "SupportsDirectStream": True,
        "DefaultAudioStreamIndex": 1,
        "DefaultSubtitleStreamIndex": 2,
        "MediaStreams": [
            _media_stream(0, "Video", "h264", "eng", "1080p H264 SDR", default=True),
            _media_stream(1, "Audio", "eac3", "eng", "English E-AC-3", channels=6, default=True),
            _media_stream(3, "Audio", "aac", "fra", "French AAC", channels=2),
            _media_stream(2, "Subtitle", "subrip", "eng", "English SRT", text_subtitle=True, default=True),
            _media_stream(0, "Subtitle", "subrip", "nld", "Dutch SRT - External", external=True, text_subtitle=True),
        ],
    }


def movie_item(item_id="movie-1", name="The Matrix", *, played=False, position=0,
               played_percentage=0.0, tag="tag-matrix") -> dict:
    item = {
        "Id": item_id,
        "Name": name,
        "Type": "Movie",
        "ProductionYear": 1999,
        "RunTimeTicks": 81600000000,
        "Overview": "A hacker learns the truth about his reality.",
        "Genres": ["Action", "Science Fiction"],
        "CommunityRating": 8.7,
        "OfficialRating": "R",
        "MediaSources": [fake_media_source(item_id)],
        "UserData": {
            "Played": played,
            "PlaybackPositionTicks": position,
            "PlayedPercentage": played_percentage,
            "PlayCount": 1 if played else 0,
        },
        "ImageTags": {"Primary": tag},
    }
    return item


def fake_audio_source(item_id: str, runtime_ticks: int = 2 * TICKS_PER_SECOND) -> dict:
    return {
        "Id": item_id,
        "Name": "Fake audio source",
        "Container": "flac",
        "RunTimeTicks": runtime_ticks,
        "SupportsDirectPlay": True,
        "SupportsDirectStream": True,
        "DefaultAudioStreamIndex": 0,
        "MediaStreams": [
            {
                "Index": 0,
                "Type": "Audio",
                "Codec": "flac",
                "Language": "eng",
                "DisplayTitle": "FLAC - Stereo",
                "Channels": 2,
                "ChannelLayout": "stereo",
                "IsDefault": True,
            }
        ],
    }


def audio_items() -> list[dict]:
    return [
        {
            "Id": f"audio-{index}",
            "Name": name,
            "Type": "Audio",
            "Album": "Test Album",
            "AlbumId": "album-test",
            "Artists": ["Test Artist"],
            "ProductionYear": 2026,
            "RunTimeTicks": 2 * TICKS_PER_SECOND,
            "MediaSources": [fake_audio_source(f"audio-{index}")],
            "ImageTags": {"Primary": "fake-audio-art"},
            "UserData": {
                "Played": False,
                "PlaybackPositionTicks": 0,
                "PlayedPercentage": 0.0,
            },
        }
        for index, name in ((1, "Track One"), (2, "Track Two"))
    ]


def series_items() -> dict[str, list[dict]]:
    series = {
        "Id": "series-1",
        "Name": "Severance",
        "Type": "Series",
        "ProductionYear": 2022,
        "ChildCount": 2,
        "Overview": "Office workers undergo a procedure that divides their memories.",
        "Genres": ["Drama", "Mystery"],
        "UserData": {"Played": False},
        "ImageTags": {"Primary": "tag-series"},
    }
    season = {
        "Id": "season-1",
        "Name": "Season 1",
        "Type": "Season",
        "SeriesName": "Severance",
        "SeriesId": "series-1",
        "IndexNumber": 1,
        "ChildCount": 3,
        "UserData": {"Played": False},
        "ImageTags": {"Primary": "tag-season"},
    }
    episode = {
        "Id": "episode-1",
        "Name": "Good News About Hell",
        "Type": "Episode",
        "SeriesName": "Severance",
        "SeriesId": "series-1",
        "SeasonName": "Season 1",
        "ParentIndexNumber": 1,
        "IndexNumber": 1,
        "RunTimeTicks": 33000000000,
        "Overview": "Mark leads a team of office workers.",
        "UserData": {"Played": False, "PlaybackPositionTicks": 0},
        "MediaSources": [fake_media_source("episode-1")],
        "ImageTags": {"Primary": "tag-episode"},
    }
    return {"series": [series], "season": [season], "episode": [episode]}


VIEWS = [
    {
        "Id": "lib-movies",
        "Name": "Movies",
        "Type": "CollectionFolder",
        "CollectionType": "movies",
    },
    {
        "Id": "lib-shows",
        "Name": "Shows",
        "Type": "CollectionFolder",
        "CollectionType": "tvshows",
    },
]

RESUME = [
    movie_item(position=1_200_000_000, played_percentage=15.0),
    movie_item("movie-2", "Arrival", played=True, tag="tag-arrival"),
]


def playable_item() -> dict:
    return {
        "Id": "playable-1",
        "Name": "Playable Test",
        "Type": "Movie",
        "ProductionYear": 2026,
        "RunTimeTicks": 12 * TICKS_PER_SECOND,
        "Overview": "A tiny generated video used by the integration tests.",
        "MediaSources": [
            fake_media_source(
                "playable-1",
                runtime_ticks=12 * TICKS_PER_SECOND,
            )
        ],
        "UserData": {
            "Played": False,
            "PlaybackPositionTicks": 0,
            "PlayedPercentage": 0.0,
        },
        "ImageTags": {},
    }

PLAYED_IDS: list[dict] = []


class FakeJellyfinServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeJellyfinHandler)
        self.requests: list[dict] = []
        self.overrides: dict[tuple[str, str], tuple[int, object]] = {}
        self.include_playable = False
        self.serve_images = False
        self.media_path: Path | None = None
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.shutdown()
        self.server_close()


class FakeJellyfinHandler(BaseHTTPRequestHandler):
    server: FakeJellyfinServer

    def log_message(self, *args) -> None:  # silence the test output
        pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        body = None
        if raw_body:
            try:
                body = json.loads(raw_body)
            except ValueError:
                body = raw_body.decode("utf-8", "replace")
        record = {
            "method": method,
            "path": parsed.path,
            "raw_path": self.path,
            "query": query,
            "headers": dict(self.headers),
            "body": body,
        }
        self.server.requests.append(record)

        override = self.server.overrides.get((method, parsed.path))
        if override:
            status, payload = override
            self._send(status, payload)
            return

        status, payload = self._route(method, parsed.path, query, body)
        if status is not None:
            self._send(status, payload)

    def _route(self, method, path, query, body):
        if method == "POST" and path == "/Users/AuthenticateByName":
            password = (body or {}).get("Pw", "")
            if password == "wrong":
                return 401, {"Message": "Invalid credentials"}
            return 200, {
                "User": {
                    "Id": "user-1",
                    "Name": (body or {}).get("Username", "tester"),
                    "Policy": {"IsAdministrator": True},
                },
                "AccessToken": "fake-token",
                "ServerId": "fake-server",
            }
        if method == "GET" and path == "/Users/Me":
            return 200, {"Id": "user-1", "Name": "tester"}
        if method == "GET" and path == "/UserViews":
            return 200, {"Items": VIEWS, "TotalRecordCount": len(VIEWS)}
        if method == "GET" and path == "/UserItems/Resume":
            items = list(RESUME)
            if self.server.include_playable:
                items.insert(0, playable_item())
            return 200, {"Items": items, "TotalRecordCount": len(items)}
        if (
            method == "GET"
            and path.startswith("/Videos/")
            and path.endswith("/stream")
        ):
            return self._stream_media()
        if (
            method == "GET"
            and path.startswith("/Audio/")
            and path.endswith("/stream")
        ):
            return self._stream_media()
        if method == "GET" and path == "/Items/Latest":
            # Deliberately a bare list: exercises compatibility with old servers.
            return 200, [movie_item("movie-3", "Latest Movie", tag="tag-latest")]
        if method == "GET" and path == "/Items":
            return self._items(query)
        if method == "GET" and path.startswith("/Items/") and path.endswith("/Images/Primary"):
            if not self.server.serve_images:
                return 404, {"Message": "No image in tests"}
            image = Path(__file__).resolve().parent.parent / "jellytui/assets/jellyfin-icon.png"
            return 200, (image.read_bytes(), "image/png")
        if method == "GET" and path.startswith("/Items/"):
            item_id = path.split("/")[2]
            for item in [*RESUME, *series_items()["series"], *series_items()["season"], *series_items()["episode"]]:
                if item["Id"] == item_id:
                    return 200, item
            return 404, {"Message": "Item not found"}
        if method == "POST" and path in {
            "/Sessions/Playing",
            "/Sessions/Playing/Progress",
            "/Sessions/Playing/Stopped",
        }:
            return 204, None
        if method in {"POST", "DELETE"} and path.startswith("/UserPlayedItems/"):
            item_id = path.split("/")[2]
            PLAYED_IDS.append({"id": item_id, "played": method == "POST"})
            return 200, {}
        return 404, {"Message": f"Unhandled route {method} {path}"}

    def _items(self, query):
        parent = (query.get("parentId") or [""])[0]
        search = (query.get("searchTerm") or [""])[0].lower()
        if search:
            matches = [
                movie_item(),
                *series_items()["series"],
                *audio_items(),
            ]
            return 200, {
                "Items": [item for item in matches if search in item["Name"].lower()],
                "TotalRecordCount": 1,
            }
        if parent == "lib-movies":
            return 200, {
                "Items": [movie_item(), movie_item("movie-2", "Arrival", played=True)],
                "TotalRecordCount": 2,
            }
        if parent == "lib-shows":
            return 200, {"Items": series_items()["series"], "TotalRecordCount": 1}
        if parent == "series-1":
            return 200, {"Items": series_items()["season"], "TotalRecordCount": 1}
        if parent == "season-1":
            return 200, {"Items": series_items()["episode"], "TotalRecordCount": 1}
        return 200, {"Items": [], "TotalRecordCount": 0}

    def _stream_media(self):
        path = self.server.media_path
        if path is None or not path.exists():
            return 404, {"Message": "No media configured"}
        data = path.read_bytes()
        range_header = self.headers.get("Range", "")
        start = 0
        end = len(data) - 1
        if range_header.startswith("bytes="):
            try:
                first, _, last = range_header[6:].partition("-")
                start = int(first) if first else 0
                if last:
                    end = min(int(last), len(data) - 1)
            except ValueError:
                start, end = 0, len(data) - 1
            if start >= len(data):
                return 416, {"Message": "Range not satisfiable"}
            chunk = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return None, None
        return 200, data

    def _send(self, status: int, payload) -> None:
        if payload is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if isinstance(payload, bytes):
            self.send_response(status)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and isinstance(payload[0], bytes)
        ):
            data, content_type = payload
            self.send_response(status)
            self.send_header("Content-Type", str(content_type))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_server():
    server = FakeJellyfinServer()
    PLAYED_IDS.clear()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    return tmp_path / "config.json"


@pytest.fixture(scope="session")
def media_file(tmp_path_factory) -> Path:
    """A small generated MKV with two audio tracks and one embedded subtitle."""
    if not shutil.which("mpv") or not shutil.which("ffmpeg"):
        pytest.skip("mpv and ffmpeg are required")
    folder = tmp_path_factory.mktemp("media")
    subtitle = folder / "english.srt"
    subtitle.write_text(
        "1\n00:00:00,500 --> 00:00:03,000\nHello from the test subtitle\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\nSecond line\n",
        encoding="utf-8",
    )
    path = folder / "multi.mkv"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=12",
        "-i", str(subtitle),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
        "-c:v", "mpeg4", "-c:a", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:0", "title=English Stereo",
        "-metadata:s:a:1", "language=jpn",
        "-metadata:s:a:1", "title=Japanese Dub",
        "-metadata:s:s:0", "language=eng",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return path


@pytest.fixture(scope="session")
def audio_file(tmp_path_factory) -> Path:
    """A short FLAC used by the ffmpeg audio and music-player tests."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    path = tmp_path_factory.mktemp("audio") / "tone.flac"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "flac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path
