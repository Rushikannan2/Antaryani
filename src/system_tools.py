"""Model-facing system, screenshot, and recording tools.

These tools are thin adapters over the same services used by the dashboard.
They do not implement a second file-management or security path: Windows file
operations remain in :mod:`windows_tools` and its existing policy layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from activity import ActivityLog
from screen_capture import ScreenshotError, capture_screenshot, screenshots_dir
from screen_recording import (
    InvalidTransitionError,
    RecordingCommand,
    RecordingError,
    RecordingManager,
    RecordingState,
    interpret_recording_intent,
)
from session_history import SessionHistory
from system_metrics import collect_system_metrics


class SystemTools:
    """Real local system capabilities exposed to the voice agent."""

    def __init__(
        self,
        *,
        history: SessionHistory | None = None,
        activity: ActivityLog | None = None,
        recorder: RecordingManager | None = None,
        screenshot_dir: str | Path | None = None,
        screenshot_grabber: Callable[..., Any] | None = None,
        metrics_fn: Callable[[], dict[str, dict[str, object]]] | None = None,
        session_id: str | None = None,
    ) -> None:
        self.history = history or SessionHistory()
        self.activity = activity or ActivityLog(history=self.history)
        self.recorder = recorder or RecordingManager(output_dir=None)
        self.screenshot_dir = (
            Path(screenshot_dir) if screenshot_dir is not None else screenshots_dir()
        )
        self.screenshot_grabber = screenshot_grabber
        self.metrics_fn = metrics_fn or collect_system_metrics
        self.session_id = session_id

    @property
    def tools(self) -> list:
        tools = [
            self.get_system_status,
            self.take_system_screenshot,
            self.control_screen_recording,
        ]
        # LiveKit currently exposes the canonical identifier as ``id`` while
        # dashboard/test consumers commonly use ``name``.  Expose both on the
        # same live FunctionTool without wrapping or duplicating it.
        for tool in tools:
            if not hasattr(tool, "name"):
                tool.name = tool.id
        return tools

    def _record(self, kind: str, status: str, detail: object) -> None:
        self.activity.record(kind, status, detail, session_id=self.session_id)

    @function_tool()
    async def get_system_status(
        self,
        context: RunContext,
        request: str = "",
    ) -> dict[str, object]:
        """Read current CPU, RAM, GPU, storage, battery, network, and time.

        Use this for questions such as battery level, RAM usage, CPU usage,
        storage space, GPU usage, or overall system status. Values come from
        native local providers; unavailable values are explicitly marked and
        are never invented.
        """

        try:
            metrics = await asyncio.to_thread(self.metrics_fn)
        except Exception as exc:
            raise ToolError(f"System metrics are unavailable: {exc}") from exc
        if not isinstance(metrics, dict):
            raise ToolError("System metrics returned an invalid response.")
        self._record("system", "ok", request or "System status requested")
        return metrics

    @function_tool()
    async def take_system_screenshot(self, context: RunContext) -> dict[str, object]:
        """Capture the actual Windows desktop and save a verified PNG file.

        This is different from the browser page screenshot: it captures the
        current display, reports the saved path, and never claims success
        unless the file exists and is non-empty.
        """

        try:
            result = await asyncio.to_thread(
                capture_screenshot,
                directory=self.screenshot_dir,
                grabber=self.screenshot_grabber,
            )
        except (ScreenshotError, OSError, RuntimeError) as exc:
            self._record("screenshot", "error", str(exc))
            raise ToolError(str(exc)) from exc
        self._record(
            "screenshot",
            "saved",
            result.get("path", result.get("filename", "screenshot")),
        )
        return result

    @function_tool()
    async def control_screen_recording(
        self,
        context: RunContext,
        command: str,
    ) -> dict[str, object]:
        """Control the real screen recorder using a natural command phrase.

        Supported commands include start recording, pause, resume or continue,
        and stop or end. Bare stop is interpreted only while a recording is
        active; ordinary conversation never stops a recording accidentally.
        """

        normalized = command.strip().casefold() if isinstance(command, str) else ""
        if (
            normalized in {"stop", "end", "finish", "stop recording", "end recording"}
            and self.recorder.state is RecordingState.IDLE
        ):
            result: dict[str, object] = {
                "ok": False,
                "error": "NO_ACTIVE_RECORDING",
                "state": self.recorder.status()["state"],
            }
            self._record("recording", "refused", "No active recording")
            return result

        intent = interpret_recording_intent(command, self.recorder.state)
        if intent is None:
            return {
                "ok": False,
                "error": "NOT_A_RECORDING_COMMAND",
                "state": self.recorder.status()["state"],
            }

        try:
            await asyncio.to_thread(self._apply_recording_command, intent)
        except (InvalidTransitionError, RecordingError, OSError, RuntimeError) as exc:
            self._record("recording", "error", str(exc))
            return {
                "ok": False,
                "error": str(exc),
                "state": self.recorder.status()["state"],
            }

        status = await asyncio.to_thread(self.recorder.status)
        status["ok"] = status.get("state") not in {"error"}
        return status

    def _apply_recording_command(self, intent: RecordingCommand) -> None:
        """Run one recorder transition off the event loop.

        Starting/stopping joins the capture thread and finalizes the video
        file; doing that on the agent loop would delay audio handling.
        """
        if intent is RecordingCommand.START:
            self.recorder.start()
            self._record("recording", "started", "Screen recording started")
        elif intent is RecordingCommand.PAUSE:
            self.recorder.pause()
            self._record("recording", "paused", "Screen recording paused")
        elif intent is RecordingCommand.RESUME:
            self.recorder.resume()
            self._record("recording", "resumed", "Screen recording resumed")
        elif intent is RecordingCommand.STOP:
            stopped = self.recorder.stop()
            if (
                stopped.get("state") == RecordingState.ERROR.value
                or stopped.get("state") is RecordingState.ERROR
            ):
                self._record(
                    "recording", "error", stopped.get("error", "Recording failed")
                )
            else:
                metadata = stopped.get("last") or {}
                self._record(
                    "recording", "saved", metadata.get("path", "Screen recording")
                )


__all__ = ["SystemTools"]
