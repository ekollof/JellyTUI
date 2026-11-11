"""mpv IPC integration tests using a generated multi-track video."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from jellytui.player import MpvPlayer, Track

pytestmark = pytest.mark.skipif(
    shutil.which("mpv") is None,
    reason="mpv is required",
)


@pytest.fixture
def player():
    instance = MpvPlayer(extra_args=["--no-config", "--vo=null", "--ao=null"])
    try:
        yield instance
    finally:
        instance.quit()


def wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_load_and_tracks(player: MpvPlayer, media_file: Path):
    player.load(media_file, start=5.0)
    assert wait_for(
        lambda: player.state().audio_tracks and player.state().duration > 0
    )
    state = player.state()
    assert state.loaded
    assert state.duration == pytest.approx(12.0, abs=0.5)
    assert state.position >= 4.8
    assert [track.id for track in state.audio_tracks] == [1, 2]
    assert len(state.subtitle_tracks) == 1
    assert "English" in state.audio_tracks[0].label
    assert "Japanese" in state.audio_tracks[1].label
    assert "mono" in state.audio_tracks[1].label
    assert state.audio_tracks[0].selected


def test_audio_subtitle_and_external_tracks(player: MpvPlayer, media_file: Path):
    player.load(media_file)
    assert wait_for(lambda: player.state().subtitle_tracks)

    player.set_audio(2)
    assert wait_for(lambda: player.state().aid == 2)

    player.set_subtitle(1)
    assert wait_for(lambda: player.state().sid == 1)

    new_id = player.add_subtitle(
        str(media_file.with_name("english.srt")),
        title="External English",
        lang="eng",
    )
    assert new_id >= 2
    assert wait_for(lambda: player.state().sid == new_id)
    external = [
        track for track in player.state().subtitle_tracks if track.external
    ]
    assert external
    assert external[0].label.endswith("external")

    player.set_subtitle(None)
    assert wait_for(lambda: player.state().sid is None)


def test_seek_pause_and_stop(player: MpvPlayer, media_file: Path):
    player.load(media_file)
    assert wait_for(lambda: player.state().loaded)
    player.set_pause(True)
    assert wait_for(lambda: player.state().paused)

    before = player.state().position
    player.seek(3.0)
    assert wait_for(lambda: player.state().position > before + 2, timeout=5)

    player.set_pause(False)
    assert wait_for(lambda: not player.state().paused)

    end_before = player.state().end_count
    player.stop()
    assert wait_for(lambda: player.state().end_count > end_before)
    assert not player.state().loaded
    assert player.state().end_reason == "stop"


def test_track_labels():
    audio = Track(
        id=1,
        type="audio",
        lang="eng",
        codec="eac3",
        channels=6,
        channel_layout="5.1(side)",
    )
    assert audio.channel_text == "5.1"
    assert "English" in audio.label
    assert "E-AC-3" in audio.label

    sub = Track(
        id=2,
        type="sub",
        lang="eng",
        title="SDH",
        codec="hdmv_pgs_subtitle",
    )
    assert "SDH" in sub.label
    assert "PGS" in sub.label
