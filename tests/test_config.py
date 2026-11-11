"""Tests for configuration persistence."""

from __future__ import annotations

import json
import stat

from jellytui.config import Config, normalise_server


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "jellytui" / "config.json"
    config = Config(
        server="http://example.test/jellyfin",
        username="alice",
        user_id="user-1",
        token="secret-token",
        audio_language="eng",
        subtitle_language="nld",
        subtitle_mode="forced",
        images=False,
    )
    config.save(path)
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    loaded = Config.load(path)
    assert loaded.server == "http://example.test/jellyfin"
    assert loaded.username == "alice"
    assert loaded.token == "secret-token"
    assert loaded.audio_language == "eng"
    assert loaded.subtitle_language == "nld"
    assert loaded.subtitle_mode == "forced"
    assert loaded.images is False
    assert loaded.is_authenticated


def test_clear_session_keeps_username():
    config = Config(username="alice", user_id="u", token="t")
    config.clear_session()
    assert config.username == "alice"
    assert config.user_id == ""
    assert config.token == ""
    assert not config.is_authenticated


def test_load_missing_or_broken_file(tmp_path):
    assert Config.load(tmp_path / "missing.json").server == "http://localhost:8096"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    config = Config.load(broken)
    assert config.token == ""
    assert config.device_id


def test_unknown_fields_are_ignored_and_defaults_filled(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"server": "http://x", "unknown": 1, "mpv_args": "nope"}),
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.server == "http://x"
    assert config.mpv_args == []
    assert config.device_id


def test_normalise_server():
    assert normalise_server("jellyfin.example:8096/jellyfin") == (
        "http://jellyfin.example:8096/jellyfin"
    )
    assert normalise_server("https://x/y/") == "https://x/y"
    for bad in ("", "   ", "http://"):
        try:
            normalise_server(bad)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"{bad!r} should be invalid")
