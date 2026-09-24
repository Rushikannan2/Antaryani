"""Real screen recording: strict state machine, MJPEG AVI writer, no shell.

Recording states are fixed by specification and enforced on every
transition. Frames come from an injected grabber in tests so no real
display is touched; in production they are grabbed with Pillow. The
container is written with pure-Python RIFF/AVI structs - no ffmpeg, no
subprocess, no model-generated commands.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import struct
import threading
import time
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from PIL import Image

from safe_files import unique_path

__all__ = [
    "VALID_TRANSITIONS",
    "InvalidTransitionError",
    "RecordingCommand",
    "RecordingError",
    "RecordingManager",
    "RecordingState",
    "can_transition",
    "interpret_recording_intent",
    "recordings_dir",
    "unique_path",
    "verify_recording",
]


class RecordingState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    ERROR = "error"


class RecordingCommand(str, Enum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


VALID_TRANSITIONS: dict[RecordingState, set[RecordingState]] = {
    RecordingState.IDLE: {RecordingState.RECORDING},
    RecordingState.RECORDING: {RecordingState.PAUSED, RecordingState.STOPPING},
    RecordingState.PAUSED: {RecordingState.RECORDING, RecordingState.STOPPING},
    RecordingState.STOPPING: {RecordingState.COMPLETED, RecordingState.ERROR},
    RecordingState.COMPLETED: {RecordingState.IDLE},
    RecordingState.ERROR: {RecordingState.IDLE},
}


class InvalidTransitionError(Exception):
    """Raised when a control command is illegal in the current state."""


class RecordingError(Exception):
    """Raised when a recording file cannot be created, written, or verified."""


def can_transition(current: RecordingState | str, target: RecordingState | str) -> bool:
    try:
        current_state = (
            current
            if isinstance(current, RecordingState)
            else RecordingState(str(current).casefold())
        )
        target_state = (
            target
            if isinstance(target, RecordingState)
            else RecordingState(str(target).casefold())
        )
    except ValueError:
        return False
    return target_state in VALID_TRANSITIONS.get(current_state, set())


def recordings_dir() -> Path:
    override = os.environ.get("SRILATHA_RECORDINGS_DIR")
    return (
        Path(override).expanduser() if override else Path.home() / "Videos" / "Srilatha"
    )


def verify_recording(path: Path | str) -> dict:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise RecordingError(f"Recording file is missing: {file_path}")
    size = file_path.stat().st_size
    if size <= 0:
        raise RecordingError(f"Recording file is empty (zero bytes): {file_path}")
    try:
        with file_path.open("rb") as stream:
            header = stream.read(12)
    except OSError as exc:
        raise RecordingError(f"Could not read recording file: {file_path}") from exc
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"AVI ":
        raise RecordingError(f"Recording file is not a valid AVI: {file_path}")
    return {"path": str(file_path), "size_bytes": size, "verified": True}


# ----------------------------------------------------------------------
# voice intent -> command
# ----------------------------------------------------------------------
def _normalize(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    lowered = text.strip().lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


_START_RE = re.compile(
    r"^(please\s+)?(start|begin)\s+(the\s+|screen\s+|my\s+)?recording$"
    r"|^record(\s+(my|the))?\s+(screen|desktop|display)(\s+recording)?$",
    re.IGNORECASE,
)
_PAUSE_RE = re.compile(r"^(please\s+)?pause(\s+(the\s+)?recording)?$", re.IGNORECASE)
_RESUME_RE = re.compile(
    r"^(please\s+)?(resume|continue)(\s+(the\s+)?recording)?$", re.IGNORECASE
)
_STOP_RE = re.compile(
    r"^(please\s+)?(stop|end|finish)(\s+(the\s+)?recording)?$", re.IGNORECASE
)


def interpret_recording_intent(
    text: Any, state: RecordingState
) -> RecordingCommand | None:
    """Map Rushi Sir's words to a recording command, or ``None``.

    The mapping is state-aware: a casual "stop" in ordinary conversation
    (no active recording) is never a recording command, and commands that
    do not apply in the current state return ``None``.
    """
    normalized = _normalize(text)
    if not normalized:
        return None

    if state in (
        RecordingState.IDLE,
        RecordingState.COMPLETED,
        RecordingState.ERROR,
    ):
        if _START_RE.match(normalized):
            return RecordingCommand.START
        return None

    if state is RecordingState.RECORDING:
        if _PAUSE_RE.match(normalized):
            return RecordingCommand.PAUSE
        if _STOP_RE.match(normalized):
            return RecordingCommand.STOP
        return None

    if state is RecordingState.PAUSED:
        if _RESUME_RE.match(normalized):
            return RecordingCommand.RESUME
        if _STOP_RE.match(normalized):
            return RecordingCommand.STOP
        return None

    return None


# ----------------------------------------------------------------------
# MJPEG AVI writer (pure Python)
# ----------------------------------------------------------------------
def _u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def _patch_u32(handle: Any, position: int, value: int) -> None:
    handle.seek(position)
    handle.write(_u32(value))


def _patch_i32(handle: Any, position: int, value: int) -> None:
    handle.seek(position)
    handle.write(struct.pack("<i", value))


class _AviWriter:
    """Streams JPEG frames into an MJPEG AVI container.

    The fixed-size header block is written first with placeholders; at
    close() the frame counts, dimensions, list sizes, and the RIFF size
    are patched in place, then an idx1 index is appended.
    """

    def __init__(self, path: Path, *, fps: float) -> None:
        self.path = Path(path)
        self._fps = max(float(fps), 0.1)
        self._frames: list[tuple[int, int]] = []  # (size, offset)
        self.width = 0
        self.height = 0
        self._max_frame = 0
        self._f = open(self.path, "x+b")  # noqa: SIM115  # exclusive create - never overwrites

        handle = self._f
        handle.write(b"RIFF")
        self._riff_pos = handle.tell()
        handle.write(_u32(0))
        handle.write(b"AVI ")

        # ---- hdrl list ----
        handle.write(b"LIST")
        hdrl_size_pos = handle.tell()
        handle.write(_u32(0))
        handle.write(b"hdrl")

        handle.write(b"avih")
        handle.write(_u32(56))
        self._avih = handle.tell()
        micro_sec = int(1_000_000 / self._fps)
        handle.write(
            struct.pack(
                "<14I",
                micro_sec,  # dwMicroSecPerFrame
                0,  # dwMaxBytesPerSec (patched)
                0,  # dwPaddingGranularity
                0x110,  # dwFlags: HASINDEX | ISINTERLEAVED
                0,  # dwTotalFrames (patched)
                0,  # dwInitialFrames
                1,  # dwStreams
                0,  # dwSuggestedBufferSize (patched)
                0,  # dwWidth (patched)
                0,  # dwHeight (patched)
                0,
                0,
                0,
                0,  # dwReserved[4]
            )
        )

        handle.write(b"LIST")
        strl_size_pos = handle.tell()
        handle.write(_u32(0))
        handle.write(b"strl")

        handle.write(b"strh")
        handle.write(_u32(56))
        self._strh = handle.tell()
        scale = 1000
        rate = round(self._fps * 1000)
        handle.write(b"vids")
        handle.write(b"MJPG")
        handle.write(
            struct.pack(
                "<IHH8I",
                0,  # dwFlags
                0,  # wPriority
                0,  # wLanguage
                0,  # dwInitialFrames
                scale,  # dwScale
                rate,  # dwRate
                0,  # dwStart
                0,  # dwLength (patched)
                0,  # dwSuggestedBufferSize (patched)
                0xFFFFFFFF,  # dwQuality (-1 = default)
                0,  # dwSampleSize
            )
        )
        handle.write(struct.pack("<4h", 0, 0, 0, 0))  # rcFrame (patched)

        handle.write(b"strf")
        handle.write(_u32(40))
        self._strf = handle.tell()
        handle.write(
            struct.pack(
                "<IiiHH4sIiiII",
                40,  # biSize
                0,  # biWidth (patched)
                0,  # biHeight (patched)
                1,  # biPlanes
                24,  # biBitCount
                b"MJPG",  # biCompression
                0,  # biSizeImage (patched)
                0,  # biXPelsPerMeter
                0,  # biYPelsPerMeter
                0,  # biClrUsed
                0,  # biClrImportant
            )
        )

        strl_end = handle.tell()
        _patch_u32(handle, strl_size_pos, strl_end - (strl_size_pos + 4))
        _patch_u32(handle, hdrl_size_pos, strl_end - (hdrl_size_pos + 4) - 0)

        # ---- movi list ----
        handle.seek(strl_end)
        handle.write(b"LIST")
        self._movi_size_pos = handle.tell()
        handle.write(_u32(0))
        self._movi_fourcc = handle.tell()
        handle.write(b"movi")

    def add_frame(self, jpeg: bytes, width: int, height: int) -> None:
        if self.width == 0:
            self.width, self.height = int(width), int(height)
        handle = self._f
        offset = handle.tell() - self._movi_fourcc
        handle.write(b"00dc")
        handle.write(_u32(len(jpeg)))
        handle.write(jpeg)
        if len(jpeg) % 2:
            handle.write(b"\x00")
        self._frames.append((len(jpeg), offset))
        if len(jpeg) > self._max_frame:
            self._max_frame = len(jpeg)

    def close(self) -> dict:
        handle = self._f
        frames = len(self._frames)

        idx_pos = handle.tell()
        handle.write(b"idx1")
        handle.write(_u32(16 * len(self._frames)))
        for size, offset in self._frames:
            handle.write(b"00dc")
            handle.write(_u32(0x10))  # AVIIF_KEYFRAME
            handle.write(_u32(offset))
            handle.write(_u32(size))

        _patch_u32(handle, self._movi_size_pos, idx_pos - (self._movi_size_pos + 4))

        _patch_u32(handle, self._avih + 4, int(self._fps * (self._max_frame or 1)))
        _patch_u32(handle, self._avih + 16, frames)
        _patch_u32(handle, self._avih + 28, self._max_frame)
        _patch_u32(handle, self._avih + 32, self.width)
        _patch_u32(handle, self._avih + 36, self.height)

        _patch_u32(handle, self._strh + 32, frames)
        _patch_u32(handle, self._strh + 36, self._max_frame)
        handle.seek(self._strh + 48)
        handle.write(struct.pack("<4h", 0, 0, self.width, self.height))

        _patch_i32(handle, self._strf + 4, self.width)
        _patch_i32(handle, self._strf + 8, self.height)
        _patch_u32(handle, self._strf + 20, self.width * self.height * 3)

        handle.flush()
        size = handle.tell()
        _patch_u32(handle, self._riff_pos, size - 8)
        handle.close()

        return {
            "path": str(self.path),
            "size_bytes": self.path.stat().st_size,
            "frames": frames,
            "width": self.width,
            "height": self.height,
            "duration_seconds": round(frames / self._fps, 3),
        }

    def abort(self) -> None:
        try:
            if not self._f.closed:
                self._f.close()
        except Exception:
            pass
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)


def _default_grab() -> Image.Image:
    from PIL import ImageGrab

    return ImageGrab.grab(all_screens=True)


# ----------------------------------------------------------------------
# recording manager
# ----------------------------------------------------------------------
class RecordingManager:
    """Owns the recording state machine, capture thread, and AVI writer."""

    def __init__(
        self,
        output_dir: Path | str | None = None,
        frame_grabber: Callable[[], Image.Image] | None = None,
        fps: float = 4.0,
        max_width: int = 1600,
    ) -> None:
        self._dir = Path(output_dir) if output_dir is not None else recordings_dir()
        self._frame_source = frame_grabber or _default_grab
        self._fps = max(float(fps), 0.1)
        self._max_width = int(max_width)

        self._lock = threading.RLock()
        self._state = RecordingState.IDLE
        self._thread: threading.Thread | None = None
        self._writer: _AviWriter | None = None
        self._path: Path | None = None
        self._frames = 0
        self._elapsed = 0.0
        self._segment_start: float | None = None
        self._stop_evt: threading.Event | None = None
        self._pause_evt: threading.Event | None = None
        self._size: tuple[int, int] | None = None
        self._last: dict | None = None
        self._error: str | None = None
        self._capture_error: str | None = None

    # ---- introspection -------------------------------------------------
    @property
    def state(self) -> RecordingState:
        with self._lock:
            return self._state

    def force_state(self, state: RecordingState) -> None:
        """Set the state directly (diagnostic hook used by tests)."""
        with self._lock:
            self._state = state

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frames

    @property
    def capture_error(self) -> str | None:
        with self._lock:
            return self._capture_error

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            return self._current_elapsed()

    def _current_elapsed(self) -> float:
        elapsed = self._elapsed
        if self._state is RecordingState.RECORDING and self._segment_start is not None:
            elapsed += time.monotonic() - self._segment_start
        return round(elapsed, 3)

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "elapsed_seconds": self._current_elapsed(),
                "last": self._last,
                "error": self._error,
                "frame_count": self._frames,
                "frames": self._frames,
            }

    # ---- transitions ---------------------------------------------------
    def _require(self, target: RecordingState) -> None:
        if not can_transition(self._state, target):
            raise InvalidTransitionError(
                f"Cannot move from {self._state.value!r} to {target.value!r}."
            )

    def _accumulate(self) -> None:
        if self._segment_start is not None:
            self._elapsed += time.monotonic() - self._segment_start
            self._segment_start = None

    def start(self) -> RecordingState:
        with self._lock:
            if self._state in (RecordingState.COMPLETED, RecordingState.ERROR):
                self._state = RecordingState.IDLE
            self._require(RecordingState.RECORDING)
            path: Path | None = None
            writer: _AviWriter | None = None
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                path = unique_path(self._dir, f"recording_{stamp}", ".avi")
                writer = _AviWriter(path, fps=self._fps)
            except Exception as exc:
                if path is not None:
                    with contextlib.suppress(OSError):
                        path.unlink(missing_ok=True)
                raise RecordingError(
                    f"Could not open the recording file: {exc}"
                ) from exc

            self._writer = writer
            self._path = path
            self._frames = 0
            self._elapsed = 0.0
            self._segment_start = time.monotonic()
            self._error = None
            self._capture_error = None
            self._size = None
            self._stop_evt = threading.Event()
            self._pause_evt = threading.Event()
            self._state = RecordingState.RECORDING
            self._thread = threading.Thread(
                target=self._capture_loop, name="srilatha-recorder", daemon=True
            )
            self._thread.start()
        return self._state

    def pause(self) -> RecordingState:
        with self._lock:
            self._require(RecordingState.PAUSED)
            self._accumulate()
            self._state = RecordingState.PAUSED
            if self._pause_evt is not None:
                self._pause_evt.set()
        return self._state

    def resume(self) -> RecordingState:
        with self._lock:
            if self._state is RecordingState.IDLE:
                raise InvalidTransitionError("There is no paused recording to resume.")
            self._require(RecordingState.RECORDING)
            self._segment_start = time.monotonic()
            self._state = RecordingState.RECORDING
            if self._pause_evt is not None:
                self._pause_evt.clear()
        return self._state

    def reset(self) -> RecordingState:
        with self._lock:
            if self._state in (
                RecordingState.IDLE,
                RecordingState.COMPLETED,
                RecordingState.ERROR,
            ):
                self._state = RecordingState.IDLE
                return self._state
            raise InvalidTransitionError(f"Cannot reset while {self._state.value!r}.")

    def stop(self) -> dict:
        with self._lock:
            self._require(RecordingState.STOPPING)
            self._accumulate()
            self._state = RecordingState.STOPPING
            thread = self._thread
            writer = self._writer
            if self._stop_evt is not None:
                self._stop_evt.set()
            if self._pause_evt is not None:
                self._pause_evt.clear()

        if thread is not None and thread.is_alive():
            thread.join(timeout=15.0)

        with self._lock:
            capture_error = self._capture_error
            if thread is not None and thread.is_alive():
                capture_error = (
                    "Recording worker did not stop within the safety timeout"
                )
            frames = self._frames
            if writer is None or capture_error is not None or frames == 0:
                if writer is not None:
                    writer.abort()
                self._state = RecordingState.ERROR
                self._error = capture_error or (
                    "No frames were captured, so no recording was saved."
                )
                self._thread = None
                return self.status()
            try:
                meta = writer.close()
                verify_recording(meta["path"])
            except Exception as exc:
                writer.abort()
                self._state = RecordingState.ERROR
                self._error = f"Recording could not be verified: {exc}"
                self._thread = None
                return self.status()
            self._state = RecordingState.COMPLETED
            self._error = None
            self._last = {**meta, "duration_seconds": round(self._elapsed, 3)}
            self._thread = None
            return self.status()

    def close(self) -> None:
        """Finalize an active capture during service shutdown."""

        if self.state in (RecordingState.RECORDING, RecordingState.PAUSED):
            self.stop()

    # ---- capture loop --------------------------------------------------
    def _encode(self, image: Image.Image) -> tuple[bytes, int, int]:
        frame = image.convert("RGB")
        if self._size is None:
            width, height = frame.size
            if width > self._max_width:
                height = max(2, int(height * (self._max_width / max(width, 1))))
                width = self._max_width
            width = max(2, width - (width % 2))
            height = max(2, height - (height % 2))
            self._size = (width, height)
        if frame.size != self._size:
            frame = frame.resize(self._size, Image.Resampling.BILINEAR)
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=70)
        return buffer.getvalue(), self._size[0], self._size[1]

    def _capture_loop(self) -> None:
        assert self._stop_evt is not None
        assert self._pause_evt is not None
        interval = 1.0 / self._fps
        while not self._stop_evt.is_set():
            if self._pause_evt.is_set():
                self._pause_evt.wait(timeout=0.1)
                continue
            started = time.monotonic()
            try:
                image = self._frame_source()
                if not isinstance(image, Image.Image):
                    raise RecordingError("The frame source did not return an image.")
                jpeg, width, height = self._encode(image)
                writer = self._writer
                if writer is None:
                    break
                writer.add_frame(jpeg, width, height)
                with self._lock:
                    self._frames += 1
            except Exception as exc:
                with self._lock:
                    if self._capture_error is None:
                        self._capture_error = str(exc) or type(exc).__name__
                break
            remaining = interval - (time.monotonic() - started)
            while remaining > 0 and not self._stop_evt.is_set():
                step = min(0.02, remaining)
                self._stop_evt.wait(timeout=step)
                remaining -= step
