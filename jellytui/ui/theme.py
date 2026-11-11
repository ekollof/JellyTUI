"""Theme and branding for JellyTUI."""

from __future__ import annotations

from rich.text import Text
from textual.theme import Theme

# Jellyfin's brand purple and blue, tuned for a dark terminal UI.
JELLYFIN_THEME = Theme(
    name="jellyfin",
    primary="#aa5cc3",
    secondary="#00a4dc",
    accent="#00a4dc",
    warning="#ffb86c",
    error="#ff6b81",
    success="#7bd88f",
    foreground="#e8e8f2",
    background="#0f0f14",
    surface="#16161e",
    panel="#1d1d28",
    boost="#262636",
    dark=True,
)

_NAME_START = (0xAA, 0x5C, 0xC3)
_NAME_END = (0x00, 0xA4, 0xDC)


def wordmark_text() -> Text:
    """The JellyTUI name, coloured with a purple-to-blue gradient."""
    text = Text()
    name = "JellyTUI"
    for index, char in enumerate(name):
        mix = index / max(1, len(name) - 1)
        red, green, blue = (
            round(a + (b - a) * mix) for a, b in zip(_NAME_START, _NAME_END, strict=True)
        )
        colour = f"#{red:02x}{green:02x}{blue:02x}"
        text.append(char, style=f"bold {colour}")
    return text


def logo_text() -> Text:
    """Text-only fallback: a play mark followed by the wordmark."""
    text = Text()
    text.append("▶", style="bold #00a4dc")
    text.append("  ")
    text.append_text(wordmark_text())
    return text


def tagline_text() -> Text:
    return Text("Jellyfin in your terminal", style="dim italic")
