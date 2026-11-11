"""Terminal graphics capability detection and chafa fallback rendering."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

# Importing these modules probes the terminal for graphics support. This must
# happen before Textual takes ownership of stdin, so keep the imports at module
# level and import this module early during startup.
from textual_image.renderable import Image as _AutoRenderable
from textual_image.renderable import SixelImage as _SixelRenderable
from textual_image.renderable import TGPImage as _TGPRenderable
from textual_image.widget import AutoImage, SixelImage, TGPImage

GraphicsMode = Literal["kitty", "sixel", "chafa", "text"]

_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
CONTROL_SEQUENCE = re.compile(r"\x1b\[\?25[lh]")


@dataclass(frozen=True)
class Graphics:
    """How images should be presented on this terminal."""

    mode: GraphicsMode
    chafa_path: str | None = None

    @property
    def graphical(self) -> bool:
        """True when the terminal can render real pixel graphics."""
        return self.mode in {"kitty", "sixel"}

    @property
    def can_show_images(self) -> bool:
        return self.mode != "text"

    @property
    def label(self) -> str:
        return {
            "kitty": "Kitty graphics protocol",
            "sixel": "Sixel graphics",
            "chafa": "chafa art",
            "text": "text only",
        }[self.mode]


@lru_cache(maxsize=1)
def detect_graphics() -> Graphics:
    """Detect graphics support, honouring JELLYTUI_GRAPHICS overrides.

    The actual protocol probe is performed by textual-image before Textual
    takes ownership of the terminal. It talks to the terminal and checks for
    the Kitty graphics protocol and Sixel support.
    """
    override = os.environ.get("JELLYTUI_GRAPHICS", "").strip().lower()
    chafa = shutil.which(os.environ.get("JELLYTUI_CHAFA", "chafa"))

    if override in {"kitty", "sixel"}:
        return Graphics(override, chafa)
    if override in {"chafa", "art"}:
        return Graphics("chafa", chafa) if chafa else Graphics("text")
    if override in {"text", "none", "off"}:
        return Graphics("text")
    if override and override != "auto":
        # Unknown values are treated as automatic detection.
        pass

    backend = _graphics_backend()
    if backend:
        return Graphics(backend, chafa)
    if chafa:
        return Graphics("chafa", chafa)
    return Graphics("text")


def _graphics_backend() -> GraphicsMode | None:
    if _AutoRenderable is _TGPRenderable:
        return "kitty"
    if _AutoRenderable is _SixelRenderable:
        return "sixel"
    return None


def image_widget_class(mode: GraphicsMode):
    """Return the textual-image widget class for a given mode."""
    if mode == "kitty":
        return TGPImage
    if mode == "sixel":
        return SixelImage
    return AutoImage


def asset_path(name: str) -> Path:
    return _ASSET_DIR / name


@lru_cache(maxsize=128)
def chafa_ansi(
    path: str,
    width: int,
    height: int,
    colors: str = "full",
) -> str | None:
    """Render an image to ANSI art with chafa, or None if unavailable."""
    graphics = detect_graphics()
    if not graphics.chafa_path:
        return None
    command = [
        graphics.chafa_path,
        "--format",
        "symbols",
        "--colors",
        colors,
        "--size",
        f"{max(1, width)}x{max(1, height)}",
        "--animate",
        "off",
        "--work",
        "6",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env={**os.environ, "TERM": "xterm-256color"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = CONTROL_SEQUENCE.sub("", result.stdout)
    if not output.strip():
        return None
    return output
