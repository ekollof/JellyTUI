"""Unit tests for models and formatting helpers."""

from __future__ import annotations

import pytest

from jellytui.models import (
    Item,
    MediaSource,
    format_clock,
    format_duration,
    format_ticks,
    format_ticks_clock,
    language_name,
)

ITEM_JSON = {
    "Id": "episode-1",
    "Name": "Good News About Hell",
    "Type": "Episode",
    "SeriesName": "Severance",
    "SeasonName": "Season 1",
    "ParentIndexNumber": 1,
    "IndexNumber": 1,
    "RunTimeTicks": 33000000000,
    "ProductionYear": 2022,
    "CommunityRating": 8.4,
    "OfficialRating": "TV-MA",
    "Genres": ["Drama", "Mystery"],
    "Overview": "Mark leads a team of office workers.",
    "ChildCount": None,
    "ImageTags": {"Primary": "abc123"},
    "UserData": {
        "Played": False,
        "PlaybackPositionTicks": 33_000_000_000 // 3,
        "PlayedPercentage": 33.3,
    },
    "MediaSources": [
        {
            "Id": "episode-1",
            "Container": "mkv",
            "RunTimeTicks": 33000000000,
            "SupportsDirectPlay": True,
            "SupportsDirectStream": True,
            "DefaultAudioStreamIndex": 1,
            "DefaultSubtitleStreamIndex": 2,
            "MediaStreams": [
                {"Index": 0, "Type": "Video", "Codec": "h264"},
                {
                    "Index": 1,
                    "Type": "Audio",
                    "Codec": "eac3",
                    "Language": "eng",
                    "Channels": 6,
                    "ChannelLayout": "5.1",
                    "DisplayTitle": "English - Dolby Digital+ - 5.1",
                    "IsDefault": True,
                },
                {
                    "Index": 3,
                    "Type": "Audio",
                    "Codec": "aac",
                    "Language": "jpn",
                    "Channels": 2,
                    "DisplayTitle": "Japanese - AAC - Stereo",
                },
                {
                    "Index": 2,
                    "Type": "Subtitle",
                    "Codec": "subrip",
                    "Language": "eng",
                    "DisplayTitle": "English - SUBRIP",
                    "IsTextSubtitleStream": True,
                    "SupportsExternalStream": True,
                    "IsDefault": True,
                },
                {
                    "Index": 0,
                    "Type": "Subtitle",
                    "Codec": "subrip",
                    "Language": "nld",
                    "DisplayTitle": "Dutch - SUBRIP - External",
                    "IsExternal": True,
                    "IsTextSubtitleStream": True,
                    "SupportsExternalStream": True,
                },
            ],
        }
    ],
}


def test_item_parsing():
    item = Item.from_json(ITEM_JSON)
    assert item.id == "episode-1"
    assert item.episode_code == "S01E01"
    assert item.title == "S01E01 · Good News About Hell"
    assert "Severance" in item.detail_subtitle
    assert "Season 1" in item.detail_subtitle
    assert "S01E01" not in item.detail_subtitle
    assert item.image_tag == "abc123"
    assert item.progress_percent == pytest.approx(33.3, abs=0.1)


def test_media_source_and_streams():
    item = Item.from_json(ITEM_JSON)
    source = item.media_source
    assert isinstance(source, MediaSource)
    assert source.container == "mkv"
    assert source.supports_direct_play
    assert [stream.index for stream in source.audio_streams] == [1, 3]
    assert [stream.index for stream in source.subtitle_streams] == [0, 2]
    assert [stream.index for stream in source.external_subtitles] == [0]
    assert [stream.index for stream in source.embedded_subtitles] == [2]
    assert source.audio_streams[0].channels == 6
    assert source.audio_streams[0].language_label == "English"
    assert source.external_subtitles[0].can_stream_as_file
    assert source.stream_by_index(99) is None


def test_status_text_for_playable_items():
    item = Item.from_json(ITEM_JSON)
    assert item.is_playable
    assert item.status_text == "33%"

    played = Item.from_json(
        {**ITEM_JSON, "UserData": {"Played": True, "PlaybackPositionTicks": 0}}
    )
    assert played.status_text == "✓"
    assert played.progress_percent == 100.0


def test_status_text_for_containers():
    series = Item.from_json(
        {"Id": "s", "Name": "Severance", "Type": "Series", "ChildCount": 2}
    )
    assert series.is_browsable
    assert series.status_text == "2 seasons"
    season = Item.from_json(
        {"Id": "s1", "Name": "Season 1", "Type": "Season", "ChildCount": 1}
    )
    assert season.status_text == "1 episode"
    album = Item.from_json(
        {"Id": "a", "Name": "Album", "Type": "MusicAlbum", "ChildCount": 9}
    )
    assert album.status_text == "9 tracks"


def test_artwork_fallbacks():
    movie = Item.from_json({"Id": "m", "Name": "Movie", "Type": "Movie", "ImageTags": {"Primary": "own"}})
    assert movie.artwork == ("m", "own")

    audio = Item.from_json(
        {
            "Id": "track",
            "Name": "Song",
            "Type": "Audio",
            "AlbumId": "album-1",
            "AlbumPrimaryImageTag": "album-art",
        }
    )
    assert audio.artwork == ("album-1", "album-art")

    episode = Item.from_json(
        {
            "Id": "ep",
            "Name": "Pilot",
            "Type": "Episode",
            "SeriesId": "series-1",
            "SeriesPrimaryImageTag": "series-art",
        }
    )
    assert episode.artwork == ("series-1", "series-art")

    assert Item.from_json({"Id": "x", "Name": "Nothing", "Type": "Movie"}).artwork is None


def test_formatting_helpers():
    assert format_duration(0) == "0s"
    assert format_duration(65) == "1m"
    assert format_duration(3665) == "1h 01m"
    assert format_clock(0) == "0:00"
    assert format_clock(65) == "1:05"
    assert format_clock(3665) == "1:01:05"
    assert format_ticks(10_000_000 * 90) == "1m"
    assert format_ticks_clock(10_000_000 * 3665) == "1:01:05"
    assert language_name("eng") == "English"
    assert language_name("en") == "English"
    assert language_name("nld") == "Dutch"
    assert language_name("") == ""
    assert language_name("xyz") == "XYZ"
