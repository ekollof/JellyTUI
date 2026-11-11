"""Live theme integration for Omarchy and pywal.

JellyTUI reads the palette files these tools write and maps them onto Textual's
theme variables. The app polls the files once a second, so changing an Omarchy
theme or running ``wal`` repaints the TUI without a restart.
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .ui.theme import JELLYFIN_THEME

ThemeSource = Literal["auto", "omarchy", "pywal", "jellyfin"]
SOURCES: tuple[str, ...] = ("auto", "omarchy", "pywal", "jellyfin")

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Palette:
    """A resolved palette, independent of Textual's Theme class."""

    key: str
    title: str
    primary: str
    secondary: str
    accent: str
    background: str
    surface: str
    panel: str
    boost: str
    foreground: str
    success: str
    warning: str
    error: str
    dark: bool = True
    path: str = ""

    @property
    def colors(self) -> tuple[str, ...]:
        return (
            self.primary,
            self.secondary,
            self.accent,
            self.background,
            self.surface,
            self.panel,
            self.boost,
            self.foreground,
            self.success,
            self.warning,
            self.error,
        )

    def same_look(self, other: Palette | None) -> bool:
        return bool(
            other
            and self.key == other.key
            and self.title == other.title
            and self.dark == other.dark
            and self.colors == other.colors
        )


JELLYFIN_PALETTE = Palette(
    key="jellyfin",
    title="Jellyfin",
    primary=JELLYFIN_THEME.primary,
    secondary=JELLYFIN_THEME.secondary,
    accent=JELLYFIN_THEME.accent,
    background=JELLYFIN_THEME.background,
    surface=JELLYFIN_THEME.surface,
    panel=JELLYFIN_THEME.panel,
    boost=JELLYFIN_THEME.boost,
    foreground=JELLYFIN_THEME.foreground,
    success=JELLYFIN_THEME.success,
    warning=JELLYFIN_THEME.warning,
    error=JELLYFIN_THEME.error,
    dark=JELLYFIN_THEME.dark,
)


# ------------------------------------------------------------------ paths


def _state_dir() -> Path:
    return Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    ).expanduser()


def _config_dir() -> Path:
    return Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ).expanduser()


def _cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _theme_slug() -> str:
    for base in (
        _state_dir() / "omarchy" / "current",
        _config_dir() / "omarchy" / "current",
    ):
        try:
            slug = (base / "theme.name").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if slug:
            return slug
    return ""


def _omarchy_paths() -> list[Path]:
    env = _env_path("JELLYTUI_OMARCHY_COLORS")
    if env:
        return [env / "colors.toml" if env.is_dir() else env]
    paths = [
        _state_dir() / "omarchy" / "current" / "theme" / "colors.toml",
        _config_dir() / "omarchy" / "current" / "theme" / "colors.toml",
    ]
    slug = _theme_slug()
    if slug:
        paths.append(_config_dir() / "omarchy" / "themes" / slug / "colors.toml")
    return paths


def omarchy_colors_path() -> Path | None:
    for path in _omarchy_paths():
        if path.is_file():
            return path
    return None


def _pywal_paths() -> list[Path]:
    env = _env_path("JELLYTUI_PYWAL_COLORS")
    if env:
        if env.is_dir():
            return [env / "colors.json", env / "colors"]
        return [env]
    base = _cache_dir() / "wal"
    return [base / "colors.json", base / "colors"]


def pywal_colors_path() -> Path | None:
    for path in _pywal_paths():
        if path.is_file():
            return path
    return None


def omarchy_theme_name() -> str:
    slug = _theme_slug()
    if slug:
        return _pretty_name(slug)
    path = omarchy_colors_path()
    if path and path.name == "colors.toml":
        return _pretty_name(path.parent.name)
    return ""


# ------------------------------------------------------------------ colors


def _hex(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) in {3, 4}:
        text = "".join(char * 2 for char in text[:3])
    if len(text) == 8:
        text = text[:6]
    if not _HEX_RE.fullmatch(text):
        return None
    return f"#{text.lower()}"


def _pick(data: dict, *keys: str) -> str | None:
    for key in keys:
        color = _hex(data.get(key))
        if color:
            return color
    return None


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _mix(first: str, second: str, ratio: float) -> str:
    left = _rgb(first)
    right = _rgb(second)
    red, green, blue = (
        round(a + (b - a) * ratio) for a, b in zip(left, right, strict=True)
    )
    return f"#{red:02x}{green:02x}{blue:02x}"


def _luminance(color: str) -> float:
    def linear(value: int) -> float:
        channel = value / 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = _rgb(color)
    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def _shades(background: str, foreground: str) -> tuple[str, str, str]:
    return (
        _mix(background, foreground, 0.045),
        _mix(background, foreground, 0.09),
        _mix(background, foreground, 0.15),
    )


def _is_dark(data: dict, background: str) -> bool:
    mode = str(data.get("mode") or "").strip().lower()
    if mode in {"dark", "light"}:
        return mode == "dark"
    return _luminance(background) < 0.45


def _pretty_name(value: str) -> str:
    return " ".join(
        part[:1].upper() + part[1:] for part in re.split(r"[-_]+", value) if part
    )


# ---------------------------------------------------------------- parsers


def read_omarchy_palette() -> Palette | None:
    path = omarchy_colors_path()
    if path is None:
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    background = _pick(data, "background", "color0")
    foreground = _pick(data, "foreground", "color7", "light_foreground")
    if not background or not foreground:
        return None
    primary = _pick(data, "accent", "bright_blue", "blue", "magenta", "color4") or foreground
    secondary = _pick(data, "blue", "accent", "cyan", "color4") or primary
    accent = _pick(data, "cyan", "bright_cyan", "magenta", "color6") or secondary
    success = _pick(data, "green", "bright_green", "color2") or foreground
    warning = _pick(data, "yellow", "orange", "bright_yellow", "color3") or foreground
    error = _pick(data, "red", "bright_red", "color1") or foreground
    surface, panel, boost = _shades(background, foreground)
    name = omarchy_theme_name() or _pretty_name(path.parent.name)
    return Palette(
        key="omarchy",
        title=f"Omarchy · {name}" if name else "Omarchy",
        primary=primary,
        secondary=secondary,
        accent=accent,
        background=background,
        surface=surface,
        panel=panel,
        boost=boost,
        foreground=foreground,
        success=success,
        warning=warning,
        error=error,
        dark=_is_dark(data, background),
        path=str(path),
    )


def _pywal_colors(data: dict) -> dict[int, str | None]:
    raw = data.get("colors") or {}
    values: dict[int, str | None] = {}
    if isinstance(raw, dict):
        for index in range(16):
            values[index] = _hex(raw.get(f"color{index}"))
    elif isinstance(raw, list):
        for index, value in enumerate(raw[:16]):
            values[index] = _hex(value)
    return values


def read_pywal_palette() -> Palette | None:
    path = pywal_colors_path()
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        special = data.get("special")
        if not isinstance(special, dict):
            special = {}
        colors = _pywal_colors(data)
        background = _hex(special.get("background")) or colors.get(0)
        foreground = _hex(special.get("foreground")) or colors.get(7)
        primary = colors.get(4) or colors.get(5) or colors.get(6)
        secondary = colors.get(5) or colors.get(6) or primary
        accent = colors.get(6) or colors.get(5) or primary
        success = colors.get(2)
        warning = colors.get(3)
        error = colors.get(1)
    else:
        values = []
        for line in text.splitlines():
            color = _hex(line)
            if color:
                values.append(color)
        if len(values) < 8:
            return None
        background, foreground = values[0], values[7]
        primary = values[4]
        secondary = values[5]
        accent = values[6]
        success = values[2]
        warning = values[3]
        error = values[1]

    if not background or not foreground:
        return None
    primary = primary or foreground
    secondary = secondary or primary
    accent = accent or secondary
    success = success or foreground
    warning = warning or foreground
    error = error or foreground
    surface, panel, boost = _shades(background, foreground)
    return Palette(
        key="pywal",
        title="pywal",
        primary=primary,
        secondary=secondary,
        accent=accent,
        background=background,
        surface=surface,
        panel=panel,
        boost=boost,
        foreground=foreground,
        success=success,
        warning=warning,
        error=error,
        dark=_luminance(background) < 0.45,
        path=str(path),
    )


def normalize_source(value: str | None) -> str:
    source = (value or "auto").strip().lower()
    return source if source in SOURCES else "auto"


def read_palette(source: str) -> Palette | None:
    if source == "omarchy":
        return read_omarchy_palette()
    if source == "pywal":
        return read_pywal_palette()
    if source == "jellyfin":
        return JELLYFIN_PALETTE
    return None


def resolve_palette(source: str = "auto") -> Palette:
    source = normalize_source(source)
    if source == "auto":
        for key in ("omarchy", "pywal"):
            palette = read_palette(key)
            if palette is not None:
                return palette
        return JELLYFIN_PALETTE
    return read_palette(source) or JELLYFIN_PALETTE


class ThemeManager:
    """Resolves the configured source and notices live palette changes."""

    def __init__(self, source: str | None = None) -> None:
        self.source = normalize_source(
            os.environ.get("JELLYTUI_THEME") or source or "auto"
        )
        self.current: Palette | None = None
        self._missing_since: float | None = None

    def resolve(self) -> Palette:
        self.current = resolve_palette(self.source)
        self._missing_since = None
        return self.current

    def poll(self) -> Palette | None:
        """Return a new palette when the active source changed, else None."""
        if self.source == "jellyfin":
            return None
        if self.current is None:
            return self.resolve()

        if self.source == "auto":
            candidate = resolve_palette("auto")
            if candidate.key == self.current.key:
                self._missing_since = None
                changed = not candidate.same_look(self.current)
                self.current = candidate
                return candidate if changed else None

            # A different source won. If the current source is still readable,
            # a higher-priority source just appeared, so switch immediately.
            # If it vanished, give it a short grace period so an atomic theme
            # switch does not briefly fall back to another palette.
            current_available = (
                self.current.key in {"omarchy", "pywal"}
                and read_palette(self.current.key) is not None
            )
            if (
                self.current.key != "jellyfin"
                and not current_available
            ):
                now = time.monotonic()
                if self._missing_since is None:
                    self._missing_since = now
                if now - self._missing_since < 5:
                    return None
            self._missing_since = None
            self.current = candidate
            return candidate

        candidate = read_palette(self.source)
        if candidate is None:
            return None
        changed = not candidate.same_look(self.current)
        self.current = candidate
        return candidate if changed else None
