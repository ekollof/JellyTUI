"""Shared artwork fetching and terminal rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..api import JellyfinClient, JellyfinError
from ..config import cache_dir
from ..models import Item
from .capabilities import chafa_ansi


@dataclass(frozen=True)
class Artwork:
    path: Path | None = None
    ansi: str | None = None


def prepare_artwork(
    client: JellyfinClient,
    item: Item,
    *,
    width: int,
    height: int,
    mode: str,
) -> Artwork:
    """Fetch an item's artwork and render it for the current terminal mode."""
    target = item.artwork
    if target is None:
        return Artwork()
    item_id, tag = target
    try:
        path = _cached_poster(item_id, tag)
        if path is None:
            fetched = client.fetch_image(item_id, tag=tag, max_height=600)
            if fetched is not None:
                data, content_type = fetched
                path = _write_poster(item_id, tag, data, content_type)
        if path is None:
            return Artwork()
        if mode == "chafa":
            ansi = chafa_ansi(str(path), width=width, height=height, colors="full")
            if ansi is None:
                return Artwork()
            return Artwork(path, ansi)
        return Artwork(path)
    except JellyfinError:
        return Artwork()
    except Exception:  # pragma: no cover - artwork must never break browsing
        return Artwork()


def _cached_poster(item_id: str, tag: str) -> Path | None:
    folder = cache_dir() / "posters"
    folder.mkdir(parents=True, exist_ok=True)
    matches = sorted(folder.glob(f"{_safe(item_id)}-{_safe(tag)}.*"))
    return matches[0] if matches else None


def _write_poster(
    item_id: str, tag: str, data: bytes, content_type: str
) -> Path | None:
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get((content_type or "").lower(), ".jpg")
    folder = cache_dir() / "posters"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe(item_id)}-{_safe(tag)}{extension}"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        return None
    return path


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", value)[:80] or "x"
