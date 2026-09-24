"""Local aiohttp API powering the Srilatha dashboard.

The API binds to localhost and exposes only sanitized metrics, verified media
controls, local history, and the existing allowlisted Windows quick action. It
contains no credential forwarding, shell execution, or second file policy.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import mimetypes
import os
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from aiohttp import web

from activity import ActivityLog
from screen_capture import (
    ScreenshotError,
    capture_screenshot,
)
from screen_capture import (
    screenshots_dir as default_screenshots_dir,
)
from screen_recording import (
    InvalidTransitionError,
    RecordingError,
    RecordingManager,
    RecordingState,
)
from screen_recording import (
    recordings_dir as default_recordings_dir,
)
from session_history import SessionHistory

logger = logging.getLogger(__name__)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
_RECORDING_ACTIONS = {"start", "pause", "resume", "stop", "reset"}
_QUICK_ACTIONS = {"open_files": "file explorer"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _response(value: Any, *, status: int = 200) -> web.Response:
    return web.json_response(_jsonable(value), status=status)


async def _call(function: Callable[..., Any], *args: Any) -> Any:
    result = function(*args)
    if inspect.isawaitable(result):
        return await result
    return result


async def _body(request: web.Request) -> dict[str, Any] | None:
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


class DashboardServices:
    """Shared service container for the voice agent and dashboard API."""

    def __init__(
        self,
        *,
        history: SessionHistory | None = None,
        activity: ActivityLog | None = None,
        recorder: RecordingManager | None = None,
        metrics_fn: Callable[[], dict[str, dict[str, object]]] | None = None,
        capture_fn: Callable[[], dict[str, object]] | None = None,
        screenshots_path: str | Path | None = None,
        recordings_path: str | Path | None = None,
        action_fn: Callable[[str], dict[str, object]] | None = None,
    ) -> None:
        from services import get_activity, get_history, get_recorder

        self.history = history or get_history()
        self.activity = activity or (
            ActivityLog(history=self.history) if history is not None else get_activity()
        )
        self.recorder = recorder or get_recorder()
        self.metrics_fn = metrics_fn
        self.capture_fn = capture_fn
        self.screenshots_path = Path(
            screenshots_path
            if screenshots_path is not None
            else default_screenshots_dir()
        )
        self.recordings_path = Path(
            recordings_path if recordings_path is not None else default_recordings_dir()
        )
        self.action_fn = action_fn

    def app(self) -> web.Application:
        return create_app(
            history=self.history,
            activity=self.activity,
            recorder=self.recorder,
            metrics_fn=self.metrics_fn,
            capture_fn=self.capture_fn,
            action_fn=self.action_fn,
            screenshots_dir=self.screenshots_path,
            recordings_dir=self.recordings_path,
        )


def create_app(
    *,
    history: SessionHistory | None = None,
    activity: ActivityLog | None = None,
    recorder: RecordingManager | None = None,
    metrics_fn: Callable[[], dict[str, dict[str, object]]] | None = None,
    capture_fn: Callable[[], dict[str, object]] | None = None,
    action_fn: Callable[[str], dict[str, object]] | None = None,
    screenshots_dir: str | Path | None = None,
    recordings_dir: str | Path | None = None,
) -> web.Application:
    """Create the API with injectable services (all default to real local ones)."""

    from services import get_history, get_recorder
    from system_metrics import collect_system_metrics

    store = history or get_history()
    feed = activity or ActivityLog(history=store)
    manager = recorder or get_recorder()
    metrics = metrics_fn or collect_system_metrics
    shots_path = (
        Path(screenshots_dir)
        if screenshots_dir is not None
        else default_screenshots_dir()
    )
    videos_path = (
        Path(recordings_dir) if recordings_dir is not None else default_recordings_dir()
    )

    def ensure_session() -> None:
        if store.current_session_id is None:
            store.begin_session()

    async def health(_: web.Request) -> web.Response:
        return _response({"ok": True, "service": "srilatha-dashboard"})

    async def system(_: web.Request) -> web.Response:
        try:
            return _response(await _call(metrics))
        except Exception as exc:
            return _response(
                {"error": f"System metrics unavailable: {exc}"}, status=503
            )

    async def recording_get(_: web.Request) -> web.Response:
        return _response(manager.status())

    async def recording_post(request: web.Request) -> web.Response:
        payload = await _body(request)
        if payload is None or not isinstance(payload.get("action"), str):
            return _response(
                {"ok": False, "error": "Request body must be JSON."}, status=400
            )
        action = payload["action"].strip().casefold()
        aliases = {
            "begin": "start",
            "continue": "resume",
            "end": "stop",
            "finish": "stop",
        }
        action = aliases.get(action, action)
        if action not in _RECORDING_ACTIONS:
            return _response({"ok": False, "error": "INVALID_ACTION"}, status=400)
        ensure_session()
        try:
            await asyncio.to_thread(getattr(manager, action))
        except InvalidTransitionError as exc:
            return _response(
                {"ok": False, "error": "INVALID_TRANSITION", "detail": str(exc)},
                status=409,
            )
        except (RecordingError, OSError, RuntimeError) as exc:
            return _response(
                {"ok": False, "error": "RECORDING_FAILED", "detail": str(exc)},
                status=500,
            )
        status = manager.status()
        state = status.get("state")
        state_value = state.value if isinstance(state, RecordingState) else str(state)
        if action in {"start", "pause", "resume"}:
            feed.record("recording", action, f"Screen recording {action}")
        elif action == "stop" and state_value == RecordingState.COMPLETED.value:
            metadata = status.get("last") or {}
            feed.record("recording", "saved", metadata.get("path", "Screen recording"))
        status["ok"] = state_value != RecordingState.ERROR.value
        return _response(
            status,
            status=500 if state_value == RecordingState.ERROR.value else 200,
        )

    async def screenshot(_: web.Request) -> web.Response:
        ensure_session()
        try:
            if capture_fn is None:
                result = await asyncio.to_thread(
                    capture_screenshot, directory=shots_path
                )
            else:
                result = await _call(capture_fn)
            if not isinstance(result, dict):
                raise ScreenshotError("Screenshot provider returned an invalid result")
            if result.get("ok") is False:
                raise ScreenshotError(
                    str(result.get("error", "Screenshot provider failed"))
                )
            saved_path = result.get("path")
            if saved_path:
                from screen_capture import verify_screenshot

                verify_screenshot(str(saved_path))
            feed.record(
                "screenshot",
                "saved",
                result.get("path", result.get("filename", "screenshot")),
            )
            return _response(result)
        except ScreenshotError as exc:
            feed.record("screenshot", "error", str(exc))
            return _response({"ok": False, "error": str(exc)}, status=500)
        except Exception as exc:
            feed.record("screenshot", "error", str(exc))
            return _response({"ok": False, "error": str(exc)}, status=500)

    async def sessions(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(int(request.query.get("limit", "30")), 200))
        except ValueError:
            limit = 30
        return _response(
            {"sessions": await asyncio.to_thread(store.list_sessions, limit)}
        )

    async def session_detail(request: web.Request) -> web.Response:
        detail = await asyncio.to_thread(
            store.get_session, request.match_info["session_id"]
        )
        if detail is None:
            return _response({"error": "not found"}, status=404)
        return _response(detail)

    async def activity_endpoint(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(int(request.query.get("limit", "50")), 200))
        except ValueError:
            limit = 50
        return _response({"activity": feed.recent(limit=limit)})

    async def dashboard(_: web.Request) -> web.Response:
        return _response(
            {
                "system": await _call(metrics),
                "recording": manager.status(),
                "sessions": store.list_sessions(limit=30),
                "activity": feed.recent(limit=30),
            }
        )

    async def action(request: web.Request) -> web.Response:
        payload = await _body(request)
        if payload is None or not isinstance(payload.get("action"), str):
            return _response(
                {"ok": False, "error": "Request body must be JSON."}, status=400
            )
        requested = payload["action"].strip().casefold()
        if requested == "system_status":
            return _response({"ok": True, "system": await _call(metrics)})
        if requested not in _QUICK_ACTIONS:
            return _response({"ok": False, "error": "INVALID_ACTION"}, status=400)
        ensure_session()
        try:
            if action_fn is None:
                import windows_fs

                result = await asyncio.to_thread(
                    windows_fs.launch_application, _QUICK_ACTIONS[requested]
                )
            else:
                result = await _call(action_fn, _QUICK_ACTIONS[requested])
            feed.record("application", "ok", "File Explorer")
            return _response(result)
        except Exception as exc:
            feed.record("application", "error", str(exc))
            return _response({"ok": False, "error": str(exc)}, status=500)

    async def media(request: web.Request) -> web.StreamResponse:
        kind = request.query.get("kind", "")
        filename = request.query.get("file", "")
        if kind == "image":
            root = shots_path
        elif kind in {"video", "recording"}:
            root = videos_path
        else:
            return _response({"ok": False, "error": "invalid kind"}, status=400)
        if (
            not filename
            or "/" in filename
            or "\\" in filename
            or Path(filename).name != filename
            or ".." in filename
        ):
            return _response({"ok": False, "error": "invalid file"}, status=400)
        try:
            root_resolved = root.resolve()
            target = (root_resolved / filename).resolve()
            target.relative_to(root_resolved)
        except (OSError, ValueError):
            return _response({"ok": False, "error": "invalid path"}, status=400)
        if not target.is_file():
            return _response({"ok": False, "error": "not found"}, status=404)
        content_type = (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        return web.FileResponse(
            target,
            headers={"Content-Type": content_type, "Cache-Control": "no-store"},
        )

    app = web.Application()
    app.add_routes(
        [
            web.get("/health", health),
            web.get("/system", system),
            web.get("/recording", recording_get),
            web.post("/recording", recording_post),
            web.post("/screenshot", screenshot),
            web.get("/sessions", sessions),
            web.get("/sessions/{session_id}", session_detail),
            web.get("/activity", activity_endpoint),
            web.get("/dashboard", dashboard),
            web.post("/action", action),
            web.get("/media", media),
        ]
    )
    return app


async def start_dashboard_server(
    app: web.Application | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
) -> web.AppRunner | None:
    """Start the API locally; a bind failure never blocks the voice agent."""

    selected_port = int(port or os.environ.get("SRILATHA_DASHBOARD_PORT", DEFAULT_PORT))
    runner = web.AppRunner(app or create_app())
    try:
        await runner.setup()
        await web.TCPSite(runner, host, selected_port).start()
    except OSError as exc:
        await runner.cleanup()
        logger.warning(
            "dashboard API unavailable on %s:%s: %s", host, selected_port, exc
        )
        return None
    logger.info("dashboard API listening on http://%s:%s", host, selected_port)
    return runner


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the API standalone for local development."""

    try:
        web.run_app(create_app(), host=host, port=port, print=lambda *args: None)
    except OSError as exc:
        logger.error("dashboard server not started on %s:%s: %s", host, port, exc)
    except Exception:
        logger.exception("dashboard server crashed")


__all__ = ["DashboardServices", "create_app", "run_server", "start_dashboard_server"]


if __name__ == "__main__":
    run_server()
