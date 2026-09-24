"""Persistent session history + activity log: isolated, sanitized, local.

Sessions live in a local SQLite file. Confirmation codes, tokens, and key
material must never reach the database.
"""

from __future__ import annotations

from pathlib import Path

from activity import ActivityLog
from session_history import SessionHistory, sanitize_detail


def test_create_and_close_session(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    sid = history.begin_session()
    assert sid
    current = history.get_session(sid)
    assert current is not None
    assert current["status"] == "active"
    assert current["start_time"]
    assert current["end_time"] is None

    closed = history.end_session(sid, status="completed")
    assert closed["status"] == "completed"
    assert closed["end_time"]
    assert closed["duration"] >= 0


def test_persist_summary_language_and_media(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    sid = history.begin_session()
    history.add_event(sid, "screenshot", "saved", "screenshot_2026-09-24_104213.png")
    history.add_event(sid, "file", "ok", "Moved Resume.pdf to Documents")
    history.add_event(sid, "confirmation", "required", "Delete folder")
    history.end_session(
        sid, status="completed", summary="Organized downloads", language="en"
    )
    stored = history.get_session(sid)
    assert stored is not None
    assert stored["summary"] == "Organized downloads"
    assert stored["language"] == "en"
    assert any("screenshot" in str(item) for item in stored["media"])
    assert any("Resume" in str(item) for item in stored["actions"])
    assert stored["confirmations"] >= 1


def test_retrieve_previous_sessions_newest_first(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    first = history.begin_session()
    history.end_session(first, summary="first")
    second = history.begin_session()
    history.end_session(second, summary="second")

    sessions = history.list_sessions()
    assert [s["id"] for s in sessions][:2] == [second, first]
    assert sessions[0]["summary"] == "second"


def test_session_isolation(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    a = history.begin_session()
    history.add_event(a, "file", "ok", "action in A")
    history.end_session(a)
    b = history.begin_session()
    history.add_event(b, "file", "ok", "action in B")
    history.end_session(b)

    detail_a = history.get_session(a)
    detail_b = history.get_session(b)
    assert detail_a is not None and detail_b is not None
    joined_a = " ".join(str(ev) for ev in detail_a["events"])
    joined_b = " ".join(str(ev) for ev in detail_b["events"])
    assert "action in A" in joined_a
    assert "action in B" not in joined_a
    assert "action in B" in joined_b
    assert "action in A" not in joined_b


def test_no_secrets_stored(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    sid = history.begin_session()
    history.add_event(
        sid,
        "confirmation",
        "required",
        "Delete folder; code 198958, token=abc123XYZ, api_key=sk-abcdefghijklmnop, "
        "password: hunter22",
    )
    history.end_session(sid)
    stored = history.get_session(sid)
    blob = str(stored)
    assert "198958" not in blob
    assert "abc123XYZ" not in blob
    assert "sk-abcdefghijklmnop" not in blob
    assert "hunter22" not in blob
    assert "[redacted]" in blob


def test_sanitize_detail_rules() -> None:
    assert "415729" not in sanitize_detail("the code is 415729")
    assert "LIVEKIT_API_SECRET=hunter2" not in sanitize_detail(
        "LIVEKIT_API_SECRET=hunter2"
    )
    assert sanitize_detail("Moved Resume.pdf") == "Moved Resume.pdf"
    assert len(sanitize_detail("x" * 5000)) <= 400


def test_activity_ring_records_without_session(tmp_path: Path) -> None:
    log = ActivityLog()
    entry = log.record("screenshot", "saved", "screenshot_1.png")
    assert entry["kind"] == "screenshot"
    assert entry["ts"]
    assert log.recent()[0]["detail"] == "screenshot_1.png"


def test_activity_attaches_to_current_session(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    log = ActivityLog(history=history)
    sid = history.begin_session()
    log.record("recording", "saved", "recording_1.avi")
    log.record("confirmation", "required", "Move folder")
    stored = history.get_session(sid)
    assert stored is not None
    kinds = [ev["kind"] for ev in stored["events"]]
    assert "recording" in kinds
    assert "confirmation" in kinds
    # confirmation codes must not be present in ring output either
    assert all("198958" not in str(ev) for ev in log.recent(limit=50))


def test_activity_order_and_limit(tmp_path: Path) -> None:
    log = ActivityLog(maxlen=5)
    for i in range(7):
        log.record("file", "ok", f"action {i}")
    recent = log.recent(limit=10)
    assert len(recent) == 5
    assert recent[0]["detail"] == "action 6"  # newest first


def test_end_session_counts_errors_and_actions(tmp_path: Path) -> None:
    history = SessionHistory(db_path=tmp_path / "history.db")
    sid = history.begin_session()
    history.add_event(sid, "file", "ok", "Moved a file")
    history.add_event(sid, "file", "error", "Could not move b file")
    history.add_event(sid, "file", "error", "Access denied on c")
    closed = history.end_session(sid)
    assert closed["actions"]
    assert len(closed["errors"]) == 2
