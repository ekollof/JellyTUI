"""Tests for the ffmpeg-based music backend."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jellytui.audio import AudioError, AudioPlayer


@pytest.fixture
def player():
    instance = AudioPlayer()
    usable, reason = instance.availability()
    if not usable:
        pytest.skip(reason)
    instance.set_volume(0)
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


def test_audio_backend_is_available(player: AudioPlayer):
    assert player.available
    assert player.output_tool in {"pw-play", "pacat", "aplay"}


def test_play_pause_seek_and_eof(player: AudioPlayer, audio_file: Path):
    player.load(audio_file, duration=2.0, title="Tone")
    assert wait_for(lambda: player.state().loaded)
    assert player.state().duration == pytest.approx(2.0)
    time.sleep(0.5)
    assert player.state().position > 0.1

    player.set_pause(True)
    assert wait_for(lambda: player.state().paused)
    time.sleep(0.3)
    paused_at = player.state().position
    time.sleep(0.6)
    assert abs(player.state().position - paused_at) < 0.2

    player.set_pause(False)
    time.sleep(0.4)
    assert player.state().position > paused_at + 0.1

    player.seek(1.0, absolute=True)
    time.sleep(0.3)
    assert 1.0 <= player.state().position < 1.9

    player.seek(1.8, absolute=True)
    assert wait_for(lambda: player.state().end_count > 0)
    state = player.state()
    assert state.end_reason == "eof"
    assert state.error is None
    assert state.position == pytest.approx(2.0, abs=0.1)


def test_stop_reports_stop(player: AudioPlayer, audio_file: Path):
    player.load(audio_file, duration=2.0)
    assert wait_for(lambda: player.state().loaded)
    player.stop()
    assert wait_for(lambda: player.state().end_reason == "stop")
    assert not player.state().loaded


def test_bad_url_fails_cleanly(player: AudioPlayer):
    with pytest.raises(AudioError):
        player.load("http://127.0.0.1:1/nope", duration=2.0, timeout=4.0)
