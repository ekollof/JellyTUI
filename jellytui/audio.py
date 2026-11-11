"""Music playback without mpv.

FFmpeg decodes the Jellyfin stream to raw float32 PCM and a native sound-server
tool (pw-play, paplay or aplay) writes it to the speakers. This backend is
intentionally audio-only: video still uses mpv, where seeking, subtitles and
hardware decoding come for free.

Using a small output process instead of PortAudio keeps ALSA diagnostics out of
the Textual screen and lets PipeWire/PulseAudio mix the stream normally.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .player import PlayerState


class AudioError(Exception):
    """Raised when the audio backend cannot start or continue."""


class AudioPlayer:
    """An ffmpeg -> sound-server audio player."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        device: str = "",
        sample_rate: int = 48000,
        channels: int = 2,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.device = device.strip()
        self.sample_rate = sample_rate
        self.channels = channels

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._seek_lock = threading.Lock()

        self._process: subprocess.Popen[bytes] | None = None
        self._output: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._generation = 0

        self._running = False
        self._connected = False
        self._loaded = False
        self._idle = True
        self._paused = False
        self._position = 0.0
        self._duration = 0.0
        self._base_position = 0.0
        self._frames_written = 0
        self._volume = 100
        self._title = ""
        self._end_count = 0
        self._end_reason: str | None = None
        self._error: str | None = None
        self._url = ""
        self._stderr_tail: deque[str] = deque(maxlen=12)
        self._output_stderr: deque[str] = deque(maxlen=12)

        self._np: Any = None
        self._output_tool: str | None = None
        self._output_binary: str | None = None
        self._import_error: str | None = None
        self._checked_dependencies = False

    # ------------------------------------------------------------- readiness

    def availability(self) -> tuple[bool, str]:
        """Return (usable, reason), checking ffmpeg, numpy and an output tool."""
        if self._checked_dependencies:
            return (self._np is not None, self._import_error or "")
        self._checked_dependencies = True
        ffmpeg = shutil.which(self.ffmpeg_path)
        if ffmpeg is None:
            self._import_error = f"ffmpeg was not found ({self.ffmpeg_path!r})"
            return False, self._import_error
        try:
            import numpy
        except Exception as exc:  # pragma: no cover - depends on system packages
            self._import_error = f"numpy is unavailable: {exc}"
            return False, self._import_error
        for tool in ("pw-play", "pacat", "aplay"):
            binary = shutil.which(tool)
            if binary:
                self._output_tool = tool
                self._output_binary = binary
                break
        if self._output_binary is None:
            self._import_error = "no sound output tool found (pw-play, pacat or aplay)"
            return False, self._import_error
        self._np = numpy
        return True, ""

    @property
    def available(self) -> bool:
        return self.availability()[0]

    @property
    def output_tool(self) -> str | None:
        self.availability()
        return self._output_tool

    def ensure_started(self) -> None:
        usable, reason = self.availability()
        if not usable:
            raise AudioError(reason)

    # --------------------------------------------------------------- playback

    def load(
        self,
        url: str | Path,
        *,
        start: float = 0.0,
        duration: float = 0.0,
        title: str = "",
        timeout: float = 20.0,
    ) -> None:
        self.ensure_started()
        with self._seek_lock:
            self._teardown(emit_end=False)
            with self._cond:
                self._generation += 1
                generation = self._generation
                self._url = str(url)
                self._duration = max(0.0, float(duration or 0.0))
                self._title = title
                self._base_position = max(0.0, float(start or 0.0))
                self._frames_written = 0
                self._position = self._base_position
                self._loaded = False
                self._idle = True
                self._paused = False
                self._running = True
                self._connected = True
                self._end_reason = None
                self._error = None
                self._stderr_tail.clear()
                self._output_stderr.clear()
                self._cond.notify_all()
            before_end = self._end_count
            try:
                self._spawn(generation, self._base_position, paused=False)
                self._wait_ready(generation, before_end, timeout)
            except AudioError:
                self._teardown(emit_end=False)
                with self._cond:
                    self._running = False
                raise
            except Exception as exc:
                self._teardown(emit_end=False)
                with self._cond:
                    self._running = False
                raise AudioError(f"Could not start audio playback: {exc}") from exc

    def stop(self) -> None:
        with self._seek_lock:
            if not self._running:
                return
            self._teardown(emit_end=True, reason="stop")

    def quit(self) -> None:
        with self._seek_lock:
            self._teardown(emit_end=False)
            with self._cond:
                self._running = False
                self._connected = False
                self._loaded = False
                self._idle = True
                self._paused = False
                self._cond.notify_all()

    def set_pause(self, paused: bool) -> None:
        paused = bool(paused)
        with self._cond:
            if paused == self._paused:
                return
            self._paused = paused
            output = self._output
            self._cond.notify_all()
        if output is None:
            return
        if paused:
            self._signal(SIGSTOP)
        else:
            self._signal(SIGCONT)

    def toggle_pause(self) -> None:
        with self._cond:
            paused = not self._paused
        self.set_pause(paused)

    def seek(self, offset: float, *, absolute: bool = False) -> None:
        with self._seek_lock:
            with self._cond:
                if not self._running or not self._url:
                    return
                target = float(offset) if absolute else self._position + float(offset)
                target = max(0.0, target)
                if self._duration > 0:
                    target = min(target, max(0.0, self._duration - 0.05))
                paused = self._paused
                url = self._url
                duration = self._duration
                title = self._title
            self._teardown(emit_end=False)
            with self._cond:
                self._generation += 1
                generation = self._generation
                self._url = url
                self._duration = duration
                self._title = title
                self._base_position = target
                self._frames_written = 0
                self._position = target
                self._loaded = False
                self._idle = True
                self._paused = paused
                self._running = True
                self._end_reason = None
                self._error = None
                self._cond.notify_all()
            before_end = self._end_count
            try:
                self._spawn(generation, target, paused=paused)
                self._wait_ready(generation, before_end, timeout=20.0)
            except AudioError:
                self._teardown(emit_end=False)
                raise
            except Exception as exc:
                self._teardown(emit_end=False)
                raise AudioError(f"Could not seek audio: {exc}") from exc

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        with self._cond:
            self._volume = volume

    def state(self) -> PlayerState:
        with self._cond:
            return PlayerState(
                running=self._running,
                connected=self._connected,
                loaded=self._loaded,
                idle=self._idle,
                paused=self._paused,
                position=self._position,
                duration=self._duration,
                title=self._title,
                audio_tracks=[],
                subtitle_tracks=[],
                aid=None,
                sid=None,
                volume=self._volume,
                end_count=self._end_count,
                end_reason=self._end_reason,
                error=self._error,
            )

    # --------------------------------------------------------------- internals

    def _spawn(self, generation: int, start: float, *, paused: bool) -> None:
        usable, reason = self.availability()
        if not usable:
            raise AudioError(reason)
        assert self._output_binary is not None

        ffmpeg = shutil.which(self.ffmpeg_path) or self.ffmpeg_path
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if start > 0.05:
            command += ["-ss", f"{start:.3f}"]
        command += [
            "-i",
            self._url,
            "-vn",
            "-sn",
            "-dn",
            "-map",
            "0:a:0?",
            "-ac",
            str(self.channels),
            "-ar",
            str(self.sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise AudioError(f"Could not start ffmpeg: {exc}") from exc

        try:
            output = subprocess.Popen(
                self._output_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
            )
        except OSError as exc:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            raise AudioError(f"Could not start {self._output_tool}: {exc}") from exc

        if paused:
            self._signal(SIGSTOP, output)

        with self._cond:
            if generation != self._generation:
                self._terminate(process)
                self._terminate(output)
                return
            self._process = process
            self._output = output
            self._paused = paused
            reader = threading.Thread(
                target=self._reader_loop,
                args=(process, output, generation, start),
                name="jellytui-audio",
                daemon=True,
            )
            self._reader = reader
            stderr_reader = threading.Thread(
                target=self._pipe_stderr,
                args=(process, self._stderr_tail),
                name="jellytui-audio-stderr",
                daemon=True,
            )
            output_stderr_reader = threading.Thread(
                target=self._pipe_stderr,
                args=(output, self._output_stderr),
                name="jellytui-audio-output-stderr",
                daemon=True,
            )
        stderr_reader.start()
        output_stderr_reader.start()
        reader.start()

    def _output_command(self) -> list[str]:
        assert self._output_binary is not None
        binary = self._output_binary
        if self._output_tool == "pw-play":
            command = [
                binary,
                "--raw",
                "--format",
                "f32",
                "--rate",
                str(self.sample_rate),
                "--channels",
                str(self.channels),
                "--latency",
                "200ms",
                "--volume",
                "1",
            ]
            if self.device:
                command += ["--target", self.device]
            command.append("-")
            return command
        if self._output_tool == "pacat":
            command = [
                binary,
                "--playback",
                "--raw",
                "--format=float32le",
                f"--rate={self.sample_rate}",
                f"--channels={self.channels}",
                "--client-name=JellyTUI",
                "--stream-name=JellyTUI",
                "--latency-msec=200",
            ]
            if self.device:
                command.append(f"--device={self.device}")
            return command
        command = [
            binary,
            "-q",
            "-t",
            "raw",
            "-f",
            "FLOAT_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            str(self.channels),
        ]
        if self.device:
            command += ["-D", self.device]
        command.append("-")
        return command

    def _reader_loop(
        self,
        process: subprocess.Popen[bytes],
        output: subprocess.Popen[bytes],
        generation: int,
        base: float,
    ) -> None:
        np = self._np
        if np is None or process.stdout is None or output.stdin is None:
            self._finalize(process, output, generation, "Audio output is unavailable")
            return
        frame_bytes = self.channels * 4
        chunk_bytes = max(frame_bytes, int(self.sample_rate / 10) * frame_bytes)
        error: str | None = None
        try:
            while True:
                with self._cond:
                    if generation != self._generation:
                        return
                data = process.stdout.read(chunk_bytes)
                if not data:
                    break
                usable = len(data) - (len(data) % frame_bytes)
                if usable <= 0:
                    continue
                samples = np.frombuffer(data[:usable], dtype=np.float32).reshape(
                    -1, self.channels
                )
                with self._cond:
                    if generation != self._generation:
                        return
                    self._loaded = True
                    self._idle = False
                    self._cond.notify_all()
                    while self._paused and generation == self._generation:
                        self._cond.wait(0.2)
                    if generation != self._generation:
                        return
                    gain = self._volume / 100.0
                if gain != 1.0:
                    samples = samples * gain
                try:
                    output.stdin.write(samples.tobytes())
                except (BrokenPipeError, OSError) as exc:
                    with self._cond:
                        if generation != self._generation:
                            return
                    error = f"Audio output failed: {exc}"
                    break
                with self._cond:
                    if generation != self._generation:
                        return
                    self._frames_written += len(samples)
                    self._position = base + (
                        self._frames_written / self.sample_rate
                    )
                    self._cond.notify_all()
        except Exception as exc:
            error = f"Audio decoding failed: {exc}"
        finally:
            if error and process.poll() is None:
                self._terminate(process)
            self._finalize(process, output, generation, error)

    def _pipe_stderr(
        self, process: subprocess.Popen[bytes], sink: deque[str]
    ) -> None:
        if process.stderr is None:
            return
        try:
            for raw_line in process.stderr:
                line = raw_line.decode("utf-8", "replace").strip()
                if line:
                    with self._cond:
                        sink.append(line)
        except Exception:
            pass

    def _finalize(
        self,
        process: subprocess.Popen[bytes],
        output: subprocess.Popen[bytes],
        generation: int,
        extra_error: str | None,
    ) -> None:
        try:
            return_code = process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._terminate(process)
            return_code = -9

        # Give the sound server a moment to play out the last buffered chunk.
        with self._cond:
            was_paused = self._paused
        if was_paused:
            self._signal(SIGCONT, output)
        try:
            if output.stdin is not None:
                output.stdin.close()
        except OSError:
            pass
        try:
            output_code = output.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._terminate(output)
            output_code = -9

        with self._cond:
            if generation != self._generation:
                return
            self._output = None
            self._process = None
            self._reader = None
            self._loaded = False
            self._idle = True
            self._paused = False
            already_ended = self._end_reason is not None
            if not already_ended:
                if extra_error:
                    self._error = extra_error
                    self._end_reason = "error"
                elif return_code != 0:
                    self._error = (
                        self._stderr_tail[-1]
                        if self._stderr_tail
                        else f"ffmpeg exited with status {return_code}"
                    )
                    self._end_reason = "error"
                elif output_code != 0:
                    self._error = (
                        self._output_stderr[-1]
                        if self._output_stderr
                        else f"{self._output_tool} exited with status {output_code}"
                    )
                    self._end_reason = "error"
                elif self._frames_written == 0:
                    self._error = (
                        self._stderr_tail[-1]
                        if self._stderr_tail
                        else "ffmpeg produced no audio"
                    )
                    self._end_reason = "error"
                else:
                    self._end_reason = "eof"
                    if self._duration > 0:
                        self._position = self._duration
                self._end_count += 1
            self._cond.notify_all()

    def _wait_ready(self, generation: int, before_end: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                if generation != self._generation:
                    raise AudioError("Audio playback was superseded")
                if self._error and not self._loaded:
                    raise AudioError(self._error)
                if self._loaded:
                    return
                if self._end_count > before_end:
                    raise AudioError(
                        self._error or "Audio stream ended before playback started"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AudioError("Timed out waiting for audio")
                self._cond.wait(min(remaining, 0.2))

    def _signal(self, sig: int, process: subprocess.Popen[bytes] | None = None) -> None:
        if os.name != "posix":
            return
        target = process or self._output
        if target is None or target.poll() is not None:
            return
        try:
            os.kill(target.pid, sig)
        except OSError:
            pass

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            # A SIGSTOPped process cannot handle SIGTERM until continued.
            try:
                os.kill(process.pid, SIGCONT)
            except OSError:
                pass
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.kill()
            except OSError:
                pass

    def _teardown(self, *, emit_end: bool, reason: str | None = None) -> None:
        with self._cond:
            self._generation += 1
            process = self._process
            output = self._output
            reader = self._reader
            self._process = None
            self._output = None
            self._reader = None
            self._paused = False
            self._loaded = False
            self._idle = True
            if emit_end:
                self._end_count += 1
                self._end_reason = reason or "stop"
            self._cond.notify_all()
        if output is not None:
            try:
                if output.stdin is not None:
                    output.stdin.close()
            except OSError:
                pass
            self._terminate(output)
        if process is not None:
            self._terminate(process)
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)


if os.name == "posix":
    SIGSTOP = signal.SIGSTOP
    SIGCONT = signal.SIGCONT
else:  # pragma: no cover - the audio backend falls back to mpv on Windows
    SIGSTOP = 0
    SIGCONT = 0
