"""Tests for Jellyfin stream-index mapping used in progress reports."""

from __future__ import annotations

from jellytui.models import Item
from jellytui.player import PlayerState
from jellytui.ui.main import PlaybackContext

ITEM = Item.from_json(
    {
        "Id": "movie-1",
        "Name": "The Matrix",
        "Type": "Movie",
        "MediaSources": [
            {
                "Id": "source-1",
                "SupportsDirectPlay": True,
                "MediaStreams": [
                    {"Index": 0, "Type": "Video"},
                    {"Index": 1, "Type": "Audio", "Language": "eng"},
                    {"Index": 3, "Type": "Audio", "Language": "fra"},
                    {"Index": 2, "Type": "Subtitle", "IsExternal": False},
                    {"Index": 9, "Type": "Subtitle", "IsExternal": True},
                ],
            }
        ],
    }
)


def make_context() -> PlaybackContext:
    source = ITEM.media_source
    assert source is not None
    return PlaybackContext(item=ITEM, source=source, play_session_id="session")


def test_audio_index_maps_mpv_ordinal_to_jellyfin_stream_index():
    context = make_context()
    assert context.audio_index(PlayerState(aid=1)) == 1
    assert context.audio_index(PlayerState(aid=2)) == 3
    assert context.audio_index(PlayerState(aid=3)) is None
    assert context.audio_index(PlayerState(aid=None)) is None


def test_subtitle_index_maps_embedded_tracks():
    context = make_context()
    assert context.subtitle_index(PlayerState(sid=1)) == 2
    assert context.subtitle_index(PlayerState(sid=2)) is None
    assert context.subtitle_index(PlayerState(sid=None)) is None


def test_subtitle_index_knows_externally_added_tracks():
    context = make_context()
    external = context.source.external_subtitles[0]
    context.external_subtitles[7] = external
    assert context.subtitle_index(PlayerState(sid=7)) == 9
    # An mpv id beyond the embedded count but not in the mapping is unknown.
    assert context.subtitle_index(PlayerState(sid=8)) is None
