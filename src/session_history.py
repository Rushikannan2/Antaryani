"""Persistent local session history in SQLite, sanitized at write time.

Confirmation codes, tokens, and key material must never reach the
database: every detail is redacted before it is stored.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["SessionHistory", "default_db_path", "sanitize_detail"]

_ACTION_KINDS = {"file", "launch", "browser", "system", "screenshot", "recording"}
_OK_STATUSES = {"ok", "saved", "started", "paused", "resumed"}
_MEDIA_KINDS = {"screenshot", "recording"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration REAL,
    status TEXT NOT NULL,
    language TEXT,
    summary TEXT,
    actions_json TEXT NOT NULL DEFAULT '[]',
    confirmations INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    media_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""

_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|pass|auth|code)\s*[=:]\s*\S+"
)
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_SIX_DIGIT_RE = re.compile(r"\b\d{6}\b")


def default_db_path() -> Path:
    base = os.environ.get("SRILATHA_DATA_DIR")
    if base:
        return Path(base) / "session_history.db"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Srilatha" / "session_history.db"
    return Path.home() / "Srilatha" / "session_history.db"


def sanitize_detail(text: Any, limit: int = 400) -> str:
    """Redact secrets and six-digit codes, collapse whitespace, truncate."""
    value = str(text)
    value = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[redacted]", value)
    value = _SK_KEY_RE.sub("[redacted]", value)
    value = _SIX_DIGIT_RE.sub("[redacted]", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[:limit]
    return value


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SessionHistory:
    """SQLite-backed session + event store; safe to share across threads."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._path = Path(db_path) if db_path is not None else default_db_path()
        self._init_lock = threading.Lock()
        self._initialized = False
        self.current_session_id: str | None = None
        with self._db() as connection:
            row = connection.execute(
                "SELECT id FROM sessions WHERE status = 'active' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                self.current_session_id = str(row["id"])

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if not self._initialized:
            with self._init_lock:
                if not self._initialized:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with self._raw() as conn:
                        conn.executescript(_SCHEMA)
                    self._initialized = True
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _raw(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- sessions ------------------------------------------------------
    def begin_session(self, *, language: str | None = None) -> str:
        session_id = uuid.uuid4().hex[:12]
        with self._db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, start_time, status, language, actions_json, "
                "errors_json, media_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    _now_iso(),
                    "active",
                    sanitize_detail(language) if language is not None else None,
                    "[]",
                    "[]",
                    "[]",
                ),
            )
        self.current_session_id = session_id
        return session_id

    def end_session(
        self,
        session_id: str,
        *,
        status: str = "completed",
        summary: str | None = None,
        language: str | None = None,
    ) -> dict:
        row = self._fetch_row(session_id)
        if row is None:
            raise KeyError(f"unknown session {session_id!r}")
        start = datetime.fromisoformat(row["start_time"])
        end = datetime.now().astimezone()
        duration = max((end - start).total_seconds(), 0.0)

        events = self._fetch_events(session_id)
        actions = [
            ev
            for ev in events
            if ev["kind"] in _ACTION_KINDS and ev["status"] in _OK_STATUSES
        ]
        errors = [ev for ev in events if ev["status"] == "error"]
        media = [ev for ev in events if ev["kind"] in _MEDIA_KINDS]
        confirmations = sum(1 for ev in events if ev["kind"] == "confirmation")

        with self._db() as conn:
            conn.execute(
                "UPDATE sessions SET end_time = ?, duration = ?, status = ?, "
                "summary = COALESCE(?, summary), language = COALESCE(?, language), "
                "actions_json = ?, confirmations = ?, errors_json = ?, media_json = ? "
                "WHERE id = ?",
                (
                    end.isoformat(timespec="seconds"),
                    duration,
                    sanitize_detail(status),
                    sanitize_detail(summary) if summary is not None else None,
                    sanitize_detail(language) if language is not None else None,
                    json.dumps(actions),
                    confirmations,
                    json.dumps(errors),
                    json.dumps(media),
                    session_id,
                ),
            )
        if self.current_session_id == session_id:
            self.current_session_id = None
        closed = self.get_session(session_id)
        assert closed is not None
        return closed

    def list_sessions(self, limit: int = 30) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, start_time, end_time, duration, status, language, summary "
                "FROM sessions ORDER BY start_time DESC, rowid DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["session_id"] = item["id"]
            result.append(item)
        return result

    def get_session(self, session_id: str) -> dict | None:
        row = self._fetch_row(session_id)
        if row is None:
            return None
        events = self._fetch_events(session_id)
        actions = [
            ev
            for ev in events
            if ev["kind"] in _ACTION_KINDS and ev["status"] in _OK_STATUSES
        ]
        errors = [ev for ev in events if ev["status"] == "error"]
        media = [ev for ev in events if ev["kind"] in _MEDIA_KINDS]
        confirmations = sum(1 for ev in events if ev["kind"] == "confirmation")
        duration: float | None = row["duration"]
        return {
            "id": row["id"],
            "session_id": row["id"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "duration": duration,
            "status": row["status"],
            "language": row["language"],
            "summary": row["summary"] or "",
            "actions": actions,
            "confirmations": confirmations,
            "errors": errors,
            "media": media,
            "events": events,
        }

    def active_session_id(self) -> str | None:
        """Return the newest still-open session, if one exists."""

        with self._db() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE status = 'active' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return None if row is None else str(row["id"])

    def close_active_sessions(self, *, status: str = "disconnected") -> None:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE status = 'active'"
            ).fetchall()
        for row in rows:
            self.end_session(str(row["id"]), status=status)

    # ---- events --------------------------------------------------------
    def add_event(
        self,
        session_id: str,
        kind: str,
        status: str,
        detail: str,
        *,
        ts: str | None = None,
    ) -> dict:
        timestamp = ts or _now_iso()
        safe_detail = sanitize_detail(detail)
        safe_kind = sanitize_detail(kind)[:80] or "event"
        safe_status = sanitize_detail(status)[:40] or "info"
        with self._db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"unknown session {session_id!r}")
            conn.execute(
                "INSERT INTO events (session_id, ts, kind, status, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, timestamp, safe_kind, safe_status, safe_detail),
            )
        return {
            "session_id": session_id,
            "ts": timestamp,
            "kind": safe_kind,
            "status": safe_status,
            "detail": safe_detail,
        }

    def _fetch_row(self, session_id: str) -> sqlite3.Row | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row

    def _fetch_events(self, session_id: str) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT ts, kind, status, detail FROM events "
                "WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        events: list[dict] = []
        for row in rows:
            event = dict(row)
            event["session_id"] = session_id
            events.append(event)
        return events
