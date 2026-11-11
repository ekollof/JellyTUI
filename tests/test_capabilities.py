"""Tests for terminal graphics capability detection and chafa fallback."""

from __future__ import annotations

import shutil

import pytest
from PIL import Image
from rich.text import Text

from jellytui.ui.capabilities import (
    asset_path,
    chafa_ansi,
    detect_graphics,
    image_widget_class,
)


@pytest.fixture(autouse=True)
def clear_detection_cache():
    detect_graphics.cache_clear()
    yield
    detect_graphics.cache_clear()


def test_logo_asset_is_valid_png():
    path = asset_path("jellyfin-icon.png")
    assert path.exists()
    with Image.open(path) as image:
        assert image.size == (536, 536)
        assert image.mode == "RGBA"


def test_force_text_mode(monkeypatch):
    monkeypatch.setenv("JELLYTUI_GRAPHICS", "text")
    graphics = detect_graphics()
    assert graphics.mode == "text"
    assert not graphics.graphical
    assert not graphics.can_show_images


def test_force_chafa_mode(monkeypatch):
    monkeypatch.setenv("JELLYTUI_GRAPHICS", "chafa")
    graphics = detect_graphics()
    if shutil.which("chafa"):
        assert graphics.mode == "chafa"
        assert graphics.can_show_images
        assert graphics.chafa_path
    else:  # pragma: no cover - only on systems without chafa
        assert graphics.mode == "text"


def test_force_kitty_mode(monkeypatch):
    monkeypatch.setenv("JELLYTUI_GRAPHICS", "kitty")
    graphics = detect_graphics()
    assert graphics.mode == "kitty"
    assert graphics.graphical
    assert graphics.can_show_images


def test_auto_detection_returns_a_valid_mode(monkeypatch):
    monkeypatch.delenv("JELLYTUI_GRAPHICS", raising=False)
    graphics = detect_graphics()
    assert graphics.mode in {"kitty", "sixel", "chafa", "text"}
    if graphics.mode == "text":
        assert not graphics.chafa_path


def test_image_widget_class():
    from textual_image.widget import AutoImage, SixelImage, TGPImage

    assert image_widget_class("kitty") is TGPImage
    assert image_widget_class("sixel") is SixelImage
    assert image_widget_class("chafa") is AutoImage
    assert image_widget_class("text") is AutoImage


@pytest.mark.skipif(not shutil.which("chafa"), reason="chafa is not installed")
def test_chafa_renders_ansi_art(monkeypatch):
    monkeypatch.setenv("JELLYTUI_GRAPHICS", "chafa")
    detect_graphics.cache_clear()
    art = chafa_ansi(str(asset_path("jellyfin-icon.png")), width=20, height=8)
    assert art is not None
    assert "\x1b[" in art
    assert len(art.splitlines()) <= 8
    text = Text.from_ansi(art)
    assert text.plain.strip()
