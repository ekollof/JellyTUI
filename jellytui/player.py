"""mpv control over its JSON IPC socket."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import language_name

# mpv reports "no track" as False on the sid/aid properties.
TrackId = int | None


class MpvError(Exception):
    """Raised when mpv cannot be started or an IPC command fails."""


@dataclass(frozen=True)
class Track:
    id: int
    type: str
    title: str = ""
    lang: str = ""
    codec: str = ""
    channels: int = 0
    channel_layout: str = ""
    external: bool = False
    selected: bool = False

    @property
    def channel_text(self) -> str:
        count = self.channels
        layout = self.channel_layout.strip()
        if layout.lower().startswith("unknown"):
            layout = ""
        if layout:
            return layout.replace("(side)", "").replace("(back)", "")
        if count <= 0:
            return ""
        return {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(
            count, f"{count}ch"
        )

    @property
    def label(self) -> str:
        bits: list[str] = []
        language = language_name(self.lang)
        title = self.title.strip()
        if language and (not title or language.lower() not in title.lower()):
            bits.append(language)
        if title:
            bits.append(title)
        if not bits:
            bits.append(language or f"Track {self.id}")
        if self.codec:
            bits.append(_codec_label(self.codec))
        if self.type == "audio" and self.channel_text:
            bits.append(self.channel_text)
        if self.external:
            bits.append("external")
        return " · ".join(bits)


@dataclass
class PlayerState:
    running: bool = False
    connected: bool = False
    loaded: bool = False
    idle: bool = True
    paused: bool = False
    position: float = 0.0
    duration: float = 0.0
    title: str = ""
    audio_tracks: list[Track] = field(default_factory=list)
    subtitle_tracks: list[Track] = field(default_factory=list)
    aid: TrackId = None
    sid: TrackId = None
    volume: int = 100
    end_count: int = 0
    end_reason: str | None = None
    error: str | None = None

    @property
    def playing(self) -> bool:
        return self.running and self.loaded and not self.paused


class MpvPlayer:
    """A long-lived mpv process controlled through --input-ipc-server."""

    def __init__(
        self,
        mpv_path: str = "mpv",
        extra_args: list[str] | None = None,
    ) -> None:
        self.mpv_path = mpv_path
        self.extra_args = list(extra_args or [])
        self._state = PlayerState()
        self._state_lock = threading.RLock()
        self._condition = threading.Condition(self._state_lock)
        self._command_lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._next_request_id = 1
        self._socket: socket.socket | None = None
        self._socket_path: Path | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._closing = False
        self._temp_dir: Path | None = None
        self._log_path: Path | None = None

    # --------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process and process.poll() is None)

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._socket is not None and self.running:
                return
            if self._socket is not None:
                self._cleanup()
            self._start_locked()

    def _start_locked(self) -> None:
        executable = shutil.which(self.mpv_path) or self.mpv_path
        if not Path(executable).exists() and shutil.which(self.mpv_path) is None:
            raise MpvError(
                f"mpv was not found ({self.mpv_path!r}). Install mpv or set mpv_path in the config."
            )
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        base = Path(runtime) if runtime and Path(runtime).is_dir() else None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="jellytui-", dir=base))
        self._socket_path = self._temp_dir / "mpv.sock"
        self._log_path = self._temp_dir / "mpv.log"

        args = [
            executable,
            f"--input-ipc-server={self._socket_path}",
            "--idle=yes",
            "--no-terminal",
            "--really-quiet",
            "--keep-open=no",
            "--osd-level=1",
            "--title=JellyTUI",
            *self.extra_args,
        ]
        with open(self._log_path, "wb") as log_file:
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise MpvError(f"mpv exited immediately.\n{self._log_tail()}")
            if self._socket_path.exists():
                break
            time.sleep(0.05)
        else:
            self._terminate_process()
            raise MpvError(f"mpv did not open its IPC socket.\n{self._log_tail()}")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self._socket_path))
        except OSError as exc:
            self._terminate_process()
            raise MpvError(f"Could not connect to mpv IPC: {exc}") from exc
        self._socket = sock
        with self._condition:
            self._state = PlayerState(running=True, connected=True)
            self._closing = False

        self._reader = threading.Thread(
            target=self._reader_loop, name="jellytui-mpv", daemon=True
        )
        self._reader.start()

        for index, prop in enumerate(
            (
                "time-pos",
                "duration",
                "pause",
                "track-list",
                "aid",
                "sid",
                "volume",
                "media-title",
                "idle-active",
                "path",
            ),
            start=1,
        ):
            self.command("observe_property", index, prop, timeout=5.0)

    def _log_tail(self, lines: int = 8) -> str:
        if not self._log_path:
            return ""
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        tail = "\n".join(text.strip().splitlines()[-lines:])
        return f"mpv said: {tail}" if tail else ""

    # ------------------------------------------------------------------- IPC

    def _reader_loop(self) -> None:
        sock = self._socket
        if sock is None:
            return
        buffer = b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    self._handle_message(message)
        except OSError:
            pass
        finally:
            self._handle_disconnect()

    def _handle_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("request_id")
        if request_id is not None:
            with self._command_lock:
                pending = self._pending.pop(int(request_id), None)
            if pending:
                event, slot = pending
                slot["error"] = message.get("error", "success")
                slot["data"] = message.get("data")
                event.set()
            return

        event = message.get("event")
        if not event:
            return
        if event == "property-change":
            self._handle_property(str(message.get("name") or ""), message.get("data"))
        elif event == "file-loaded":
            with self._condition:
                self._state.loaded = True
                self._state.end_reason = None
                self._state.error = None
                self._state.idle = False
                self._condition.notify_all()
        elif event == "start-file":
            with self._condition:
                self._state.loaded = False
                self._state.error = None
                self._state.end_reason = None
                self._state.position = 0.0
        elif event == "end-file":
            reason = str(message.get("reason") or "unknown")
            error = None
            if reason == "error":
                error = str(message.get("file_error") or "mpv could not open the stream")
            with self._condition:
                self._state.end_count += 1
                self._state.end_reason = reason
                self._state.loaded = False
                self._state.idle = True
                self._state.paused = False
                if error:
                    self._state.error = error
                self._condition.notify_all()
        elif event == "shutdown":
            self._handle_disconnect()

    def _handle_property(self, name: str, data: Any) -> None:
        with self._condition:
            state = self._state
            if name == "time-pos":
                if data is not None:
                    state.position = float(data)
            elif name == "duration":
                if data is not None:
                    state.duration = float(data)
            elif name == "pause":
                state.paused = bool(data)
            elif name == "idle-active":
                state.idle = bool(data)
            elif name == "aid":
                state.aid = _track_id(data)
            elif name == "sid":
                state.sid = _track_id(data)
            elif name == "volume":
                if data is not None:
                    state.volume = int(float(data))
            elif name in {"media-title", "path"}:
                if data and (name == "media-title" or not state.title):
                    state.title = str(data)
            elif name == "track-list" and isinstance(data, list):
                tracks = [_parse_track(track) for track in data]
                state.audio_tracks = [t for t in tracks if t.type == "audio"]
                state.subtitle_tracks = [t for t in tracks if t.type == "sub"]
                state.aid = next(
                    (t.id for t in state.audio_tracks if t.selected), state.aid
                )
                state.sid = next(
                    (t.id for t in state.subtitle_tracks if t.selected), state.sid
                )
            self._condition.notify_all()

    def _handle_disconnect(self) -> None:
        error = "mpv exited unexpectedly."
        with self._condition:
            self._state.connected = False
            self._state.loaded = False
            self._state.running = False
            self._state.idle = True
            self._state.error = self._state.error or error
            self._condition.notify_all()
        with self._command_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for event, slot in pending:
            slot["error"] = "mpv is no longer running"
            event.set()

    def command(self, name: str, *args: Any, timeout: float = 8.0) -> Any:
        sock = self._socket
        if sock is None or not self.running:
            raise MpvError("mpv is not running.")
        with self._command_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            event = threading.Event()
            slot: dict[str, Any] = {}
            self._pending[request_id] = (event, slot)
            payload = json.dumps(
                {"command": [name, *args], "request_id": request_id}
            ).encode("utf-8") + b"\n"
            try:
                sock.sendall(payload)
            except OSError as exc:
                self._pending.pop(request_id, None)
                raise MpvError(f"Lost connection to mpv: {exc}") from exc
        if not event.wait(timeout):
            with self._command_lock:
                self._pending.pop(request_id, None)
            raise MpvError(f"mpv did not answer the {name!r} command in time.")
        if slot.get("error") not in (None, "success"):
            raise MpvError(f"mpv command {name!r} failed: {slot['error']}")
        return slot.get("data")

    def set_property(self, name: str, value: Any) -> None:
        self.command("set_property", name, value)

    def get_property(self, name: str) -> Any:
        return self.command("get_property", name)

    # --------------------------------------------------------------- playback

    def load(self, url: str | Path, *, start: float = 0.0, timeout: float = 45.0) -> None:
        """Load a URL and wait until mpv has parsed it."""
        self.ensure_started()
        url = str(url)
        start = max(0.0, float(start or 0.0))
        with self._condition:
            end_before = self._state.end_count
            self._state.loaded = False
            self._state.error = None
            self._state.end_reason = None
        options: dict[str, str] = {"pause": "no"}
        if start > 0.5:
            options["start"] = f"{start:.3f}"
        # Signature: loadfile <url> [<flags> [<index> [<options>]]]
        try:
            self.command("loadfile", url, "replace", -1, options)
        except MpvError:
            # Older mpv builds may not accept the index/options arguments.
            self.command("loadfile", url, "replace")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._state.loaded:
                if self._state.end_count != end_before:
                    reason = self._state.end_reason or "unknown"
                    error = self._state.error
                    raise MpvError(
                        error
                        or f"mpv could not play this item (end-file reason: {reason})."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.command("stop")
                    raise MpvError("Timed out waiting for mpv to open the stream.")
                self._condition.wait(min(remaining, 0.5))
        if start > 0.5:
            # loadfile's start option usually handles this; verify and correct.
            with self._condition:
                position = self._state.position
            if position < start - 5:
                self.command("seek", start, "absolute")

    def stop(self) -> None:
        if not self.running:
            return
        try:
            self.command("stop", timeout=3.0)
        except MpvError:
            pass

    def add_subtitle(
        self, url: str | Path, *, title: str = "", lang: str = "", timeout: float = 20.0
    ) -> int:
        """Add a subtitle URL, select it, and return mpv's new subtitle track id."""
        self.ensure_started()
        url = str(url)
        with self._condition:
            known = {track.id for track in self._state.subtitle_tracks}
        args: list[Any] = ["sub-add", url, "select"]
        if title or lang:
            args.append(title)
        if lang:
            args.append(lang)
        result = self.command("sub-add", *args[1:], timeout=timeout)
        new_id = result if isinstance(result, int) else None
        deadline = time.monotonic() + 5.0
        with self._condition:
            while True:
                new_tracks = [
                    track
                    for track in self._state.subtitle_tracks
                    if track.id not in known
                ]
                if new_id is not None:
                    new_tracks = [t for t in new_tracks if t.id == new_id] or new_tracks
                if new_tracks:
                    chosen = new_tracks[0]
                    self._state.sid = chosen.id
                    return chosen.id
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(min(remaining, 0.2))
        if new_id is not None:
            return new_id
        raise MpvError("mpv did not report the newly added subtitle track.")

    def set_subtitle(self, track_id: TrackId) -> None:
        self.set_property("sid", "no" if track_id is None else int(track_id))

    def set_audio(self, track_id: TrackId) -> None:
        if track_id is None:
            self.set_property("aid", "no")
        else:
            self.set_property("aid", int(track_id))

    def set_pause(self, paused: bool) -> None:
        self.set_property("pause", bool(paused))

    def toggle_pause(self) -> None:
        self.command("cycle", "pause")

    def seek(self, offset: float, *, absolute: bool = False) -> None:
        self.command("seek", float(offset), "absolute" if absolute else "relative")

    def set_volume(self, volume: int) -> None:
        self.set_property("volume", max(0, min(130, int(volume))))

    def state(self) -> PlayerState:
        with self._state_lock:
            state = self._state
            return PlayerState(
                running=state.running,
                connected=state.connected,
                loaded=state.loaded,
                idle=state.idle,
                paused=state.paused,
                position=state.position,
                duration=state.duration,
                title=state.title,
                audio_tracks=list(state.audio_tracks),
                subtitle_tracks=list(state.subtitle_tracks),
                aid=state.aid,
                sid=state.sid,
                volume=state.volume,
                end_count=state.end_count,
                end_reason=state.end_reason,
                error=state.error,
            )

    # ------------------------------------------------------------------ close

    def quit(self) -> None:
        with self._start_lock:
            self._closing = True
            if self._process and self._process.poll() is None:
                try:
                    self.command("quit", timeout=2.0)
                except MpvError:
                    pass
                try:
                    self._process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._terminate_process()
            self._cleanup()

    def _terminate_process(self) -> None:
        process = self._process
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

    def _cleanup(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._terminate_process()
        self._process = None
        with self._condition:
            self._state = PlayerState()
            self._condition.notify_all()
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._socket_path = None
        self._log_path = None


def _track_id(value: Any) -> TrackId:
    if value is False or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


CODEC_LABELS = {
    "hdmv_pgs_subtitle": "PGS",
    "dvd_subtitle": "VobSub",
    "dvdsub": "VobSub",
    "subrip": "SRT",
    "text": "SRT",
    "ass": "ASS",
    "ssa": "ASS",
    "webvtt": "VTT",
    "mov_text": "MP4 text",
    "eac3": "E-AC-3",
    "ac3": "AC-3",
    "truehd": "TrueHD",
    "dts": "DTS",
    "aac": "AAC",
    "flac": "FLAC",
    "opus": "Opus",
    "vp9": "VP9",
    "av1": "AV1",
    "hevc": "HEVC",
    "h264": "H.264",
}


def _codec_label(codec: str) -> str:
    lowered = codec.lower()
    return CODEC_LABELS.get(lowered, codec.upper())


def _parse_track(data: dict[str, Any]) -> Track:
    return Track(
        id=int(data.get("id") or 0),
        type=str(data.get("type") or ""),
        title=str(data.get("title") or ""),
        lang=str(data.get("lang") or ""),
        codec=str(data.get("codec") or ""),
        channels=int(data.get("audio-channels") or 0),
        channel_layout=str(data.get("demux-channels") or ""),
        external=bool(data.get("external")),
        selected=bool(data.get("selected")),
    )
