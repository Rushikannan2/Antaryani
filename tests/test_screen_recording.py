"""Screen recording: strict state machine, real files, voice intent.

Frames come from an injected grabber, so tests never touch a real display.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from screen_recording import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    RecordingCommand,
    RecordingError,
    RecordingManager,
    RecordingState,
    can_transition,
    interpret_recording_intent,
    recordings_dir,
    verify_recording,
)


def _frame() -> Image.Image:
    return Image.new("RGB", (64, 36), (5, 5, 5))


def wait_until(condition, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


# ----------------------------------------------------------------------
# state machine
# ----------------------------------------------------------------------
def test_transition_matrix_matches_specification() -> None:
    expected = {
        RecordingState.IDLE: {RecordingState.RECORDING},
        RecordingState.RECORDING: {RecordingState.PAUSED, RecordingState.STOPPING},
        RecordingState.PAUSED: {RecordingState.RECORDING, RecordingState.STOPPING},
        RecordingState.STOPPING: {RecordingState.COMPLETED, RecordingState.ERROR},
        RecordingState.COMPLETED: {RecordingState.IDLE},
        RecordingState.ERROR: {RecordingState.IDLE},
    }
    assert expected == VALID_TRANSITIONS
    for current in RecordingState:
        for target in RecordingState:
            assert can_transition(current, target) is (target in expected[current])


@pytest.mark.parametrize(
    ("method", "state"),
    [
        ("pause", RecordingState.IDLE),
        ("resume", RecordingState.IDLE),
        ("stop", RecordingState.IDLE),
        ("start", RecordingState.RECORDING),
        ("pause", RecordingState.PAUSED),
        ("resume", RecordingState.RECORDING),
        ("stop", RecordingState.COMPLETED),
    ],
)
def test_invalid_transitions_rejected(
    method: str, state: RecordingState, tmp_path: Path
) -> None:
    mgr = RecordingManager(output_dir=tmp_path, frame_grabber=_frame, fps=50)
    mgr.force_state(state)
    with pytest.raises(InvalidTransitionError):
        getattr(mgr, method)()


# ----------------------------------------------------------------------
# voice intent mapping
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "phrase",
    [
        "start recording",
        "begin recording",
        "start screen recording",
        "record my screen",
    ],
)
def test_intent_start_from_idle(phrase: str) -> None:
    assert (
        interpret_recording_intent(phrase, RecordingState.IDLE)
        is RecordingCommand.START
    )


@pytest.mark.parametrize(
    "phrase", ["pause", "pause recording", "pause the recording", "please pause"]
)
def test_intent_pause_while_recording(phrase: str) -> None:
    assert (
        interpret_recording_intent(phrase, RecordingState.RECORDING)
        is RecordingCommand.PAUSE
    )


@pytest.mark.parametrize(
    "phrase", ["resume", "continue", "resume recording", "continue recording"]
)
def test_intent_resume_while_paused(phrase: str) -> None:
    assert (
        interpret_recording_intent(phrase, RecordingState.PAUSED)
        is RecordingCommand.RESUME
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "stop",
        "end",
        "finish",
        "stop recording",
        "end recording",
        "finish recording",
        "stop.",
    ],
)
def test_intent_stop_while_active(phrase: str) -> None:
    assert (
        interpret_recording_intent(phrase, RecordingState.RECORDING)
        is RecordingCommand.STOP
    )
    assert (
        interpret_recording_intent(phrase, RecordingState.PAUSED)
        is RecordingCommand.STOP
    )


def test_unrelated_stop_does_not_stop_recording() -> None:
    # No recording is active: a casual "stop" (or an unrelated sentence
    # containing it) must not be read as a recording command.
    assert interpret_recording_intent("stop", RecordingState.IDLE) is None
    assert (
        interpret_recording_intent("please stop by the store", RecordingState.IDLE)
        is None
    )
    assert interpret_recording_intent("let's stop there", RecordingState.IDLE) is None
    assert interpret_recording_intent("stop the music", RecordingState.IDLE) is None
    # ...and even with an active recording, an unrelated sentence is not a command.
    assert (
        interpret_recording_intent("stop by the store", RecordingState.RECORDING)
        is None
    )


def test_state_incompatible_intents_return_none() -> None:
    assert interpret_recording_intent("pause", RecordingState.IDLE) is None
    assert interpret_recording_intent("resume", RecordingState.RECORDING) is None
    assert (
        interpret_recording_intent("start recording", RecordingState.RECORDING) is None
    )
    assert interpret_recording_intent("stop", RecordingState.STOPPING) is None
    assert (
        interpret_recording_intent("start recording", RecordingState.STOPPING) is None
    )


def test_bare_start_without_context_returns_none() -> None:
    # "start"/"begin" alone carry no recording context while idle.
    assert interpret_recording_intent("start", RecordingState.IDLE) is None
    assert interpret_recording_intent("begin", RecordingState.IDLE) is None


# ----------------------------------------------------------------------
# real files
# ----------------------------------------------------------------------
def test_recording_lifecycle_creates_verified_file(tmp_path: Path) -> None:
    mgr = RecordingManager(output_dir=tmp_path, frame_grabber=_frame, fps=50)
    assert mgr.state is RecordingState.IDLE

    assert mgr.start() is RecordingState.RECORDING
    wait_until(lambda: mgr.frame_count >= 2)

    assert mgr.pause() is RecordingState.PAUSED
    frozen = mgr.frame_count
    time.sleep(0.15)
    assert mgr.frame_count == frozen  # nothing captured while paused

    assert mgr.resume() is RecordingState.RECORDING
    wait_until(lambda: mgr.frame_count > frozen)

    status = mgr.stop()
    assert status["state"] is RecordingState.COMPLETED
    meta = status["last"]
    path = Path(meta["path"])
    assert path.exists()
    assert path.stat().st_size > 0
    assert meta["size_bytes"] == path.stat().st_size
    assert meta["frames"] >= 1
    assert path.read_bytes()[:4] == b"RIFF"  # a real playable AVI container
    verify_recording(path)

    assert mgr.reset() is RecordingState.IDLE
    # A second recording gets a different filename - never overwritten.
    assert mgr.start() is RecordingState.RECORDING
    wait_until(lambda: mgr.frame_count >= 1)
    status2 = mgr.stop()
    assert status2["last"]["path"] != meta["path"]


def test_recording_duplicate_filename_prevented(tmp_path: Path) -> None:
    existing = tmp_path / "recording.avi"
    existing.write_bytes(b"old")
    from screen_recording import unique_path as _unique  # shared naming helper

    candidate = _unique(tmp_path, "recording", ".avi")
    assert candidate != existing
    assert existing.read_bytes() == b"old"


def test_zero_byte_recording_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "clip.avi"
    empty.write_bytes(b"")
    with pytest.raises(RecordingError):
        verify_recording(empty)
    missing = tmp_path / "nope.avi"
    with pytest.raises(RecordingError):
        verify_recording(missing)


def test_capture_failure_ends_in_error_state(tmp_path: Path) -> None:
    def boom() -> Image.Image:
        raise RuntimeError("DXGI device lost")

    mgr = RecordingManager(output_dir=tmp_path, frame_grabber=boom, fps=50)
    mgr.start()
    wait_until(lambda: mgr.capture_error is not None, timeout=5.0)
    status = mgr.stop()
    assert status["state"] is RecordingState.ERROR
    assert "DXGI device lost" in (status["error"] or "")
    assert mgr.state is RecordingState.ERROR
    assert mgr.reset() is RecordingState.IDLE


def test_status_shape_reports_state_and_elapsed(tmp_path: Path) -> None:
    mgr = RecordingManager(output_dir=tmp_path, frame_grabber=_frame, fps=50)
    status = mgr.status()
    assert status["state"] == "idle"
    assert status["elapsed_seconds"] == 0.0
    assert status["last"] is None
    assert recordings_dir().name.lower() == "srilatha"
