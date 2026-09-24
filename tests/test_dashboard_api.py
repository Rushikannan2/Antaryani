"""Dashboard API: real endpoints over real services, no mock data.

The Next.js dashboard talks to this aiohttp app through a same-origin
rewrite. Every endpoint returns live service output; secrets never appear.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from activity import ActivityLog
from dashboard_api import create_app
from screen_capture import ScreenshotError
from screen_recording import RecordingManager
from session_history import SessionHistory

FAKE_METRICS = {
    "cpu": {"available": True, "percent": 12.5, "cores": 8, "threads": 16},
    "ram": {"available": True, "percent": 40.0, "total_gb": 32.0},
    "battery": {"available": True, "percent": 88.0, "status": "discharging"},
    "gpu": {"available": False, "detail": "GPU metrics unavailable"},
}


async def _client(**deps) -> TestClient:
    app = create_app(**deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.fixture
def history(tmp_path: Path) -> SessionHistory:
    return SessionHistory(db_path=tmp_path / "history.db")


async def test_system_endpoint_returns_live_metrics(history: SessionHistory) -> None:
    client = await _client(history=history, metrics_fn=lambda: FAKE_METRICS)
    try:
        resp = await client.get("/system")
        assert resp.status == 200
        data = await resp.json()
        assert data["cpu"]["percent"] == 12.5
        assert data["gpu"]["detail"] == "GPU metrics unavailable"
    finally:
        await client.close()


async def test_recording_lifecycle_via_api(
    history: SessionHistory, tmp_path: Path
) -> None:
    frames = {"n": 0}

    def grab() -> Image.Image:
        frames["n"] += 1
        return Image.new("RGB", (64, 36), (0, 0, 0))

    recorder = RecordingManager(output_dir=tmp_path / "vid", frame_grabber=grab, fps=50)
    client = await _client(history=history, recorder=recorder)
    try:
        resp = await client.get("/recording")
        assert (await resp.json())["state"] == "idle"

        resp = await client.post("/recording", json={"action": "pause"})
        assert resp.status == 409  # invalid transition from idle

        resp = await client.post("/recording", json={"action": "not-an-action"})
        assert resp.status == 400

        resp = await client.post("/recording", json={"action": "start"})
        assert resp.status == 200
        assert (await resp.json())["state"] == "recording"

        import asyncio

        await asyncio.sleep(0.2)
        resp = await client.post("/recording", json={"action": "pause"})
        assert (await resp.json())["state"] == "paused"
        resp = await client.post("/recording", json={"action": "resume"})
        assert (await resp.json())["state"] == "recording"
        resp = await client.post("/recording", json={"action": "stop"})
        body = await resp.json()
        assert body["state"] == "completed"
        path = Path(body["last"]["path"])
        assert path.exists()
        assert path.stat().st_size > 0
    finally:
        await client.close()
        if recorder.state.value in ("recording", "paused"):
            recorder.stop()


async def test_screenshot_endpoint_success_and_failure(
    history: SessionHistory, tmp_path: Path
) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png-bytes")

    def ok_capture() -> dict:
        return {
            "ok": True,
            "path": str(shot),
            "filename": shot.name,
            "size_bytes": shot.stat().st_size,
            "displays": [{"index": 0}],
        }

    client = await _client(history=history, capture_fn=ok_capture)
    try:
        resp = await client.post("/screenshot")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["filename"] == "shot.png"
    finally:
        await client.close()

    def failing() -> dict:
        raise ScreenshotError("display is locked")

    client = await _client(history=history, capture_fn=failing)
    try:
        resp = await client.post("/screenshot")
        assert resp.status == 500
        body = await resp.json()
        assert body["ok"] is False
        assert "display is locked" in body["error"]
    finally:
        await client.close()


async def test_sessions_endpoints(history: SessionHistory) -> None:
    sid = history.begin_session()
    history.add_event(sid, "file", "ok", "Moved Resume.pdf to Documents")
    history.end_session(sid, summary="File organization")

    client = await _client(history=history)
    try:
        resp = await client.get("/sessions")
        body = await resp.json()
        assert body["sessions"][0]["id"] == sid

        resp = await client.get(f"/sessions/{sid}")
        assert resp.status == 200
        detail = await resp.json()
        assert detail["summary"] == "File organization"
        assert detail["events"]

        resp = await client.get("/sessions/does-not-exist")
        assert resp.status == 404
    finally:
        await client.close()


async def test_activity_endpoint_never_leaks_codes(history: SessionHistory) -> None:
    log = ActivityLog(history=history)
    history.begin_session()
    log.record("confirmation", "required", "Delete folder code 198958")
    client = await _client(history=history, activity=log)
    try:
        resp = await client.get("/activity")
        body = await resp.json()
        assert body["activity"]
        assert "198958" not in str(body)
        assert "[redacted]" in str(body)
    finally:
        await client.close()


async def test_media_traversal_rejected(
    history: SessionHistory, tmp_path: Path
) -> None:
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "ok.png").write_bytes(b"image-bytes")
    (tmp_path / "secret.txt").write_bytes(b"top secret")

    client = await _client(
        history=history, screenshots_dir=shots, recordings_dir=tmp_path
    )
    try:
        resp = await client.get("/media", params={"kind": "image", "file": "ok.png"})
        assert resp.status == 200
        assert await resp.read() == b"image-bytes"

        for evil in ("../secret.txt", "..%2fsecret.txt", "nope.png"):
            resp = await client.get("/media", params={"kind": "image", "file": evil})
            assert resp.status in (400, 404), evil
    finally:
        await client.close()


async def test_action_endpoint_validates_and_runs_real_action(
    history: SessionHistory,
) -> None:
    calls: list[str] = []

    def fake_action(name: str) -> dict:
        calls.append(name)
        return {"ok": True, "launched": name}

    client = await _client(history=history, action_fn=fake_action)
    try:
        resp = await client.post("/action", json={"action": "run-virus"})
        assert resp.status == 400

        resp = await client.post("/action", json={"action": "open_files"})
        assert resp.status == 200
        assert calls == ["file explorer"]
    finally:
        await client.close()


async def test_health_endpoint(history: SessionHistory) -> None:
    client = await _client(history=history)
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        assert (await resp.json())["ok"] is True
    finally:
        await client.close()
