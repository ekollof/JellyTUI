"""Configuration and session persistence for JellyTUI."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

APP_NAME = "jellytui"
DEFAULT_SERVER = "http://localhost:8096"


def default_config_path() -> Path:
    override = os.environ.get("JELLYTUI_CONFIG")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME / "config.json"


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalise_server(value: str) -> str:
    """Turn user-entered server addresses into a clean base URL."""
    value = value.strip()
    if not value:
        raise ValueError("empty server")
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = "http://" + value
    parsed = urlsplit(value)
    if not parsed.netloc:
        raise ValueError("invalid server")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc}{path}"


@dataclass
class Config:
    server: str = DEFAULT_SERVER
    username: str = ""
    user_id: str = ""
    token: str = ""
    device_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mpv_path: str = "mpv"
    mpv_args: list[str] = field(default_factory=list)
    audio_backend: str = "auto"  # auto | ffmpeg | mpv
    audio_device: str = ""
    ffmpeg_path: str = "ffmpeg"
    audio_language: str = ""
    subtitle_language: str = ""
    subtitle_mode: str = "auto"  # auto | off | forced
    report_progress: bool = True
    images: bool = True
    theme: str = "auto"
    watch_theme: bool = True

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token and self.user_id)

    @property
    def server_name(self) -> str:
        return self.server.rstrip("/")

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        path = Path(path) if path else default_config_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        values = {k: v for k, v in raw.items() if k in known}
        if not values.get("device_id"):
            values["device_id"] = str(uuid.uuid4())
        if not isinstance(values.get("mpv_args"), list):
            values["mpv_args"] = []
        return cls(**values)

    def clear_session(self) -> None:
        self.token = ""
        self.user_id = ""
        # Keep the username so the next login is quicker.
