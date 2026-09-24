"""Model-facing system tools: screenshot, recording control, status.

These three tools back the dashboard's quick actions and the voice
commands ("take a screenshot", "start recording", "what is my battery?").
They reuse the same services the dashboard uses - one source of truth.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from livekit.agents.llm import ToolError
from PIL import Image

from activity import ActivityLog
from screen_recording import RecordingManager, RecordingState
from session_history import SessionHistory
from system_tools import SystemTools


def make_context() -> SimpleNamespace:
    return SimpleNamespace(session=SimpleNamespace(current_agent=None))


def _frame() -> Image.Image:
    return Image.new("RGB", (64, 36), (1, 2, 3))


def wait_until(condition, timeout: float = 8.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return
        time_module = __import__("time")
        time_module.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def _tools(tmp_path: Path, **overrides) -> SystemTools:
    history = overrides.pop("history", None) or SessionHistory(
        db_path=tmp_path / "history.db"
    )
    activity = overrides.pop("activity", None) or ActivityLog(history=history)
    recorder = overrides.pop(
        "recorder",
        None,
    ) or RecordingManager(output_dir=tmp_path / "vid", frame_grabber=_frame, fps=50)
    defaults = {
        "history": history,
        "activity": activity,
        "recorder": recorder,
        "screenshot_dir": tmp_path / "shots",
        "metrics_fn": lambda: {"cpu": {"available": True, "percent": 7.0}},
    }
    defaults.update(overrides)
    return SystemTools(**defaults), history, activity, recorder


async def test_tool_surface_has_exactly_three_tools(tmp_path: Path) -> None:
    tools, *_ = _tools(tmp_path)
    names = {t.name for t in tools.tools}
    assert names == {
        "get_system_status",
        "take_system_screenshot",
        "control_screen_recording",
    }


async def test_get_system_status_returns_live_metrics(tmp_path: Path) -> None:
    tools, *_ = _tools(tmp_path)
    result = await tools.get_system_status(make_context())
    assert result["cpu"]["percent"] == 7.0


async def test_get_system_status_runs_metrics_off_event_loop(tmp_path: Path) -> None:
    """psutil sampling sleeps and native calls must never block the agent loop."""
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def blocking_metrics() -> dict[str, dict[str, object]]:
        seen.append(threading.get_ident())
        return {"cpu": {"percent": 42.0}}

    tools, *_ = _tools(tmp_path, metrics_fn=blocking_metrics)
    result = await tools.get_system_status(make_context(), request="cpu")
    assert result["cpu"]["percent"] == 42.0
    assert seen, "the metrics provider must run"
    assert seen[0] != loop_thread


async def test_screenshot_tool_creates_file_and_activity(tmp_path: Path) -> None:
    tools, history, activity, _ = _tools(
        tmp_path,
        screenshot_grabber=lambda: (
            Image.new("RGB", (64, 36)),
            [{"index": 0, "primary": True, "bounds": [0, 0, 64, 36]}],
        ),
    )
    sid = history.begin_session()
    result = await tools.take_system_screenshot(make_context())
    assert result["ok"] is True
    path = Path(result["path"])
    assert path.exists()
    assert path.stat().st_size > 0

    # recorded in the current session and in the activity feed
    stored = history.get_session(sid)
    assert stored is not None
    assert any(ev["kind"] == "screenshot" for ev in stored["events"])
    assert any(entry["kind"] == "screenshot" for entry in activity.recent())
    assert path.name in str(activity.recent())


async def test_screenshot_failure_surfaces_actual_reason(tmp_path: Path) -> None:
    def boom():
        raise RuntimeError("session is locked")

    tools, *_ = _tools(tmp_path, screenshot_grabber=boom)
    with pytest.raises(ToolError) as excinfo:
        await tools.take_system_screenshot(make_context())
    assert "session is locked" in str(excinfo.value)


async def test_recording_tool_start_and_stop(tmp_path: Path) -> None:
    tools, _, activity, recorder = _tools(tmp_path)

    refused = await tools.control_screen_recording(make_context(), command="stop")
    assert refused["ok"] is False
    assert refused["error"] == "NO_ACTIVE_RECORDING"
    assert recorder.state is RecordingState.IDLE

    started = await tools.control_screen_recording(
        make_context(), command="start recording"
    )
    assert started["ok"] is True
    assert started["state"] == "recording"

    async def frames_seen() -> int:
        return recorder.frame_count

    wait_until(lambda: recorder.frame_count >= 1)

    stopped = await tools.control_screen_recording(make_context(), command="stop")
    assert stopped["ok"] is True
    assert stopped["state"] == "completed"
    path = Path(stopped["last"]["path"])
    assert path.exists() and path.stat().st_size > 0
    assert any(entry["kind"] == "recording" for entry in activity.recent())
    recorder.reset()


async def test_control_screen_recording_runs_recorder_off_event_loop(
    tmp_path: Path,
) -> None:
    """Recorder transitions (thread joins, AVI finalization) must not block the loop."""
    loop_thread = threading.get_ident()
    recorder_threads: list[int] = []

    class _ThreadRecordingRecorder:
        state = RecordingState.IDLE

        def start(self) -> RecordingState:
            recorder_threads.append(threading.get_ident())
            self.state = RecordingState.RECORDING
            return self.state

        def status(self) -> dict[str, object]:
            state = self.state
            return {
                "state": state.value if isinstance(state, RecordingState) else state
            }

    tools, *_ = _tools(tmp_path, recorder=_ThreadRecordingRecorder())
    result = await tools.control_screen_recording(
        make_context(), command="start recording"
    )
    assert result["ok"] is True
    assert recorder_threads, "the recorder transition must run"
    assert recorder_threads[0] != loop_thread


async def test_recording_tool_pause_resume_via_voice_phrases(tmp_path: Path) -> None:
    tools, _, _, recorder = _tools(tmp_path)
    await tools.control_screen_recording(make_context(), command="begin recording")
    wait_until(lambda: recorder.frame_count >= 1)

    paused = await tools.control_screen_recording(
        make_context(), command="pause the recording"
    )
    assert paused["ok"] is True
    assert paused["state"] == "paused"

    resumed = await tools.control_screen_recording(make_context(), command="continue")
    assert resumed["ok"] is True
    assert resumed["state"] == "recording"

    stopped = await tools.control_screen_recording(
        make_context(), command="end recording"
    )
    assert stopped["state"] == "completed"
    recorder.reset()
