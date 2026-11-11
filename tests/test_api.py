"""Unit tests for the Jellyfin API client."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from jellytui.api import AuthenticationError, JellyfinClient, JellyfinError
from jellytui.models import Item


def make_client(server, **kwargs) -> JellyfinClient:
    return JellyfinClient(
        server.url,
        token=kwargs.pop("token", "test-token"),
        user_id=kwargs.pop("user_id", "user-1"),
        device_id=kwargs.pop("device_id", "device-1"),
        **kwargs,
    )


def last_request(server, method, path):
    matches = [
        request
        for request in server.requests
        if request["method"] == method and request["path"] == path
    ]
    assert matches, f"no {method} {path} request was made"
    return matches[-1]


def test_login_sends_auth_header_without_token(fake_server):
    client = JellyfinClient(fake_server.url, device_id="device-1")
    user = client.login("alice", "secret")

    assert user["Name"] == "alice"
    assert client.token == "fake-token"
    assert client.user_id == "user-1"

    request = last_request(fake_server, "POST", "/Users/AuthenticateByName")
    header = request["headers"]["Authorization"]
    assert header.startswith("MediaBrowser ")
    assert 'Client="JellyTUI"' in header
    assert 'DeviceId="device-1"' in header
    assert "Token=" not in header
    assert request["body"] == {"Username": "alice", "Pw": "secret"}


def test_login_rejects_bad_password(fake_server):
    client = JellyfinClient(fake_server.url, device_id="device-1")
    with pytest.raises(AuthenticationError):
        client.login("alice", "wrong")


def test_get_views_and_item_fields(fake_server):
    client = make_client(fake_server)
    views = client.get_views()
    assert [view.name for view in views] == ["Movies", "Shows"]
    assert views[0].collection_type == "movies"

    items = client.get_items(
        parent_id="lib-movies", include_types="Movie", sort_by="SortName"
    )
    assert [item.name for item in items] == ["The Matrix", "Arrival"]

    request = last_request(fake_server, "GET", "/Items")
    query = request["query"]
    assert query["userId"] == ["user-1"]
    assert query["parentId"] == ["lib-movies"]
    assert query["includeItemTypes"] == ["Movie"]
    assert query["enableImages"] == ["true"]
    assert query["enableImageTypes"] == ["Primary"]
    assert query["enableUserData"] == ["true"]
    assert "MediaSources" in query["fields"][0].split(",")
    assert "MediaStreams" in query["fields"][0].split(",")

    matrix = items[0]
    assert matrix.production_year == 1999
    source = matrix.media_source
    assert source is not None
    assert source.supports_direct_play
    assert len(source.audio_streams) == 2
    assert [s.index for s in source.audio_streams] == [1, 3]
    assert len(source.subtitle_streams) == 2
    assert len(source.external_subtitles) == 1
    assert source.external_subtitles[0].index == 0
    assert matrix.progress_percent == 0.0
    assert matrix.image_tag == "tag-matrix"


def test_get_resume_and_latest(fake_server):
    client = make_client(fake_server)
    resume = client.get_resume(limit=5)
    assert [item.name for item in resume] == ["The Matrix", "Arrival"]
    assert resume[1].played

    latest = client.get_latest(limit=10)
    # The fake server returns a bare list for /Items/Latest.
    assert [item.name for item in latest] == ["Latest Movie"]


def test_stream_urls(fake_server):
    client = make_client(fake_server)
    item = Item.from_json(
        {
            "Id": "movie-1",
            "Name": "The Matrix",
            "Type": "Movie",
            "MediaSources": [
                {
                    "Id": "source-1",
                    "SupportsDirectPlay": True,
                    "MediaStreams": [],
                }
            ],
        }
    )
    source = item.media_source
    assert source is not None

    url = client.stream_url(item, source, play_session_id="session-1")
    parsed = urlparse(url)
    assert parsed.path == "/Videos/movie-1/stream"
    query = parse_qs(parsed.query)
    assert query["static"] == ["true"]
    assert query["mediaSourceId"] == ["source-1"]
    assert query["api_key"] == ["test-token"]
    assert query["playSessionId"] == ["session-1"]
    assert query["deviceId"] == ["device-1"]

    audio = Item.from_json({"Id": "audio-1", "Name": "Song", "Type": "Audio"})
    audio_url = client.stream_url(audio, None)
    assert urlparse(audio_url).path == "/Audio/audio-1/stream"

    subtitle_url = client.subtitle_url("movie-1", source, 0, "subrip")
    parsed_sub = urlparse(subtitle_url)
    assert parsed_sub.path == "/Videos/movie-1/source-1/Subtitles/0/Stream.srt"
    assert parse_qs(parsed_sub.query)["api_key"] == ["test-token"]

    pgs = client.subtitle_url("movie-1", source, 3, "PGSSUB")
    assert urlparse(pgs).path.endswith("/Stream.sup")


def test_progress_reports(fake_server):
    client = make_client(fake_server)
    item = Item.from_json(
        {
            "Id": "movie-1",
            "Name": "The Matrix",
            "Type": "Movie",
            "MediaSources": [
                {
                    "Id": "source-1",
                    "SupportsDirectPlay": True,
                    "MediaStreams": [],
                }
            ],
        }
    )
    source = item.media_source

    client.report_start(
        item,
        source,
        play_session_id="session-1",
        position_ticks=0,
        audio_index=1,
        subtitle_index=2,
    )
    start = last_request(fake_server, "POST", "/Sessions/Playing")
    assert start["body"]["ItemId"] == "movie-1"
    assert start["body"]["MediaSourceId"] == "source-1"
    assert start["body"]["PlayMethod"] == "DirectPlay"
    assert start["body"]["AudioStreamIndex"] == 1
    assert start["body"]["SubtitleStreamIndex"] == 2
    assert start["body"]["PositionTicks"] == 0

    client.report_progress(
        item,
        source,
        play_session_id="session-1",
        position_ticks=123_456_789,
        paused=True,
        audio_index=3,
        subtitle_index=None,
        volume=80,
    )
    progress = last_request(fake_server, "POST", "/Sessions/Playing/Progress")
    assert progress["body"]["PositionTicks"] == 123_456_789
    assert progress["body"]["IsPaused"] is True
    assert progress["body"]["AudioStreamIndex"] == 3
    assert "SubtitleStreamIndex" not in progress["body"]
    assert progress["body"]["VolumeLevel"] == 80

    client.report_stopped(
        item,
        source,
        play_session_id="session-1",
        position_ticks=456,
    )
    stopped = last_request(fake_server, "POST", "/Sessions/Playing/Stopped")
    assert stopped["body"]["PositionTicks"] == 456
    assert stopped["body"]["IsPaused"] is False


def test_mark_played(fake_server):
    client = make_client(fake_server)
    client.mark_played("movie-1", True)
    request = last_request(fake_server, "POST", "/UserPlayedItems/movie-1")
    assert request["query"]["userId"] == ["user-1"]

    client.mark_played("movie-1", False)
    assert last_request(fake_server, "DELETE", "/UserPlayedItems/movie-1")


def test_http_errors_are_wrapped(fake_server):
    client = make_client(fake_server)
    fake_server.overrides[("GET", "/UserViews")] = (500, {"Message": "boom"})
    with pytest.raises(JellyfinError) as excinfo:
        client.get_views()
    assert excinfo.value.status == 500
    assert "boom" in str(excinfo.value)

    fake_server.overrides[("GET", "/UserViews")] = (401, {"Message": "nope"})
    with pytest.raises(AuthenticationError):
        client.get_views()


def test_session_token_is_sent_after_login(fake_server):
    client = JellyfinClient(fake_server.url, device_id="device-1")
    client.login("alice", "secret")
    client.get_views()
    request = last_request(fake_server, "GET", "/UserViews")
    assert 'Token="fake-token"' in request["headers"]["Authorization"]


def test_fetch_image_without_artwork_returns_none(fake_server):
    client = make_client(fake_server)
    assert client.fetch_image("movie-1") is None
