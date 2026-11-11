"""Tests for Omarchy and pywal palette parsing and live detection."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jellytui.theming import (
    JELLYFIN_PALETTE,
    ThemeManager,
    normalize_source,
    omarchy_colors_path,
    pywal_colors_path,
    read_omarchy_palette,
    read_pywal_palette,
    resolve_palette,
)

OMARCHY = """\
mode = "dark"
accent = "#ff7059"
background = "#101112"
foreground = "#f4dcc3"
dark_background = "#090a0b"
lighter_background = "#292b2c"
red = "#d7503f"
green = "#d9ad6f"
yellow = "#f5c080"
cyan = "#6ab8de"
blue = "#6284af"
magenta = "#b66d68"
orange = "#e68860"
"""


@pytest.fixture(autouse=True)
def isolated_theme_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("JELLYTUI_THEME", raising=False)
    monkeypatch.delenv("JELLYTUI_OMARCHY_COLORS", raising=False)
    monkeypatch.delenv("JELLYTUI_PYWAL_COLORS", raising=False)


@pytest.fixture
def omarchy_file(tmp_path) -> Path:
    path = tmp_path / "state" / "omarchy" / "current" / "theme" / "colors.toml"
    path.parent.mkdir(parents=True)
    path.write_text(OMARCHY, encoding="utf-8")
    (path.parent.parent / "theme.name").write_text("test-theme", encoding="utf-8")
    return path


def write_pywal_json(tmp_path, *, background="#0c0e0f", foreground="#f4dcc3") -> Path:
    path = tmp_path / "cache" / "wal" / "colors.json"
    path.parent.mkdir(parents=True)
    colors = {
        "color0": background,
        "color1": "#d7503f",
        "color2": "#d9ad6f",
        "color3": "#f5c080",
        "color4": "#6284af",
        "color5": "#b66d68",
        "color6": "#6ab8de",
        "color7": foreground,
        "color8": "#61686b",
        "color9": "#ff7059",
        "color10": "#f9c977",
        "color11": "#ffc958",
        "color12": "#83a8df",
        "color13": "#e38f88",
        "color14": "#84daff",
        "color15": "#ffe3ab",
    }
    path.write_text(
        json.dumps(
            {
                "special": {"background": background, "foreground": foreground},
                "colors": colors,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_omarchy_palette(omarchy_file):
    palette = read_omarchy_palette()
    assert palette is not None
    assert palette.key == "omarchy"
    assert palette.title == "Omarchy · Test Theme"
    assert palette.primary == "#ff7059"
    assert palette.secondary == "#6284af"
    assert palette.accent == "#6ab8de"
    assert palette.background == "#101112"
    assert palette.foreground == "#f4dcc3"
    assert palette.dark is True
    assert palette.surface != palette.background
    assert palette.warning == "#f5c080"
    assert palette.success == "#d9ad6f"
    assert omarchy_colors_path() == omarchy_file


def test_omarchy_light_mode(omarchy_file):
    omarchy_file.write_text(
        OMARCHY.replace('mode = "dark"', 'mode = "light"').replace(
            '#101112', '#eff1f5'
        ),
        encoding="utf-8",
    )
    palette = read_omarchy_palette()
    assert palette is not None
    assert palette.dark is False
    from jellytui.theming import _luminance

    assert _luminance(palette.surface) < _luminance(palette.background)


def test_pywal_json(tmp_path):
    write_pywal_json(tmp_path)
    palette = read_pywal_palette()
    assert palette is not None
    assert palette.key == "pywal"
    assert palette.primary == "#6284af"
    assert palette.secondary == "#b66d68"
    assert palette.accent == "#6ab8de"
    assert palette.background == "#0c0e0f"
    assert palette.foreground == "#f4dcc3"
    assert pywal_colors_path() is not None


def test_pywal_colors_list(tmp_path):
    path = tmp_path / "cache" / "wal" / "colors"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "#0E1112",
                "#592C29",
                "#763434",
                "#BC3A30",
                "#435381",
                "#A26A41",
                "#9E7C52",
                "#F3DCC6",
                "#232627",
                "#592C29",
                "#763434",
                "#BC3A30",
                "#435381",
                "#A26A41",
                "#9E7C52",
                "#F3DCC6",
            ]
        ),
        encoding="utf-8",
    )
    palette = read_pywal_palette()
    assert palette is not None
    assert palette.background == "#0e1112"
    assert palette.foreground == "#f3dcc6"
    assert palette.primary == "#435381"


def test_auto_priority_and_fallback(tmp_path, omarchy_file):
    write_pywal_json(tmp_path)
    assert resolve_palette("auto").key == "omarchy"

    omarchy_file.unlink()
    assert resolve_palette("auto").key == "pywal"

    (tmp_path / "cache" / "wal" / "colors.json").unlink()
    assert resolve_palette("auto").key == "jellyfin"
    assert resolve_palette("auto").same_look(JELLYFIN_PALETTE)


def test_explicit_source_missing_falls_back(tmp_path):
    assert resolve_palette("omarchy").key == "jellyfin"
    assert resolve_palette("pywal").key == "jellyfin"


def test_normalize_source():
    assert normalize_source(None) == "auto"
    assert normalize_source("PYWAL") == "pywal"
    assert normalize_source("nonsense") == "auto"


def test_manager_detects_live_changes(omarchy_file):
    manager = ThemeManager("omarchy")
    first = manager.resolve()
    assert first.key == "omarchy"
    assert manager.poll() is None

    omarchy_file.write_text(
        OMARCHY.replace("#ff7059", "#00ff00").replace("#101112", "#202020"),
        encoding="utf-8",
    )
    updated = manager.poll()
    assert updated is not None
    assert updated.primary == "#00ff00"
    assert updated.background == "#202020"
    assert manager.poll() is None


def test_auto_manager_switches_after_missing_grace(tmp_path, omarchy_file):
    write_pywal_json(tmp_path)
    manager = ThemeManager("auto")
    assert manager.resolve().key == "omarchy"

    omarchy_file.unlink()
    assert manager.poll() is None  # within the grace period
    manager._missing_since = time.monotonic() - 10
    switched = manager.poll()
    assert switched is not None
    assert switched.key == "pywal"


def test_invalid_omarchy_file_is_ignored(omarchy_file):
    omarchy_file.write_text("not = [valid", encoding="utf-8")
    assert read_omarchy_palette() is None


def test_auto_switches_to_omarchy_when_it_appears(tmp_path):
    write_pywal_json(tmp_path)
    manager = ThemeManager("auto")
    assert manager.resolve().key == "pywal"

    omarchy_path = tmp_path / "state" / "omarchy" / "current" / "theme" / "colors.toml"
    omarchy_path.parent.mkdir(parents=True)
    omarchy_path.write_text(OMARCHY, encoding="utf-8")

    updated = manager.poll()
    assert updated is not None
    assert updated.key == "omarchy"
