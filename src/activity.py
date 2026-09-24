"""In-memory activity ring: newest-first feed for the dashboard.

Every entry is sanitized at record time; when a session is active the
entry is also persisted to that session's event log.
"""

from __future__ import annotations

import contextlib
import threading
from collections import deque
from datetime import datetime

from session_history import SessionHistory, sanitize_detail

__all__ = ["ActivityLog"]


class ActivityLog:
    def __init__(
        self, maxlen: int = 200, history: SessionHistory | None = None
    ) -> None:
        self._ring: deque[dict] = deque(maxlen=maxlen)
        self.history = history
        self._lock = threading.RLock()

    def record(
        self,
        kind: str,
        status: str,
        detail: str,
        *,
        session_id: str | None = None,
    ) -> dict:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = {
            "id": f"{timestamp}-{len(self._ring)}",
            "ts": timestamp,
            "timestamp": timestamp,
            "kind": sanitize_detail(kind),
            "status": sanitize_detail(status),
            "detail": sanitize_detail(detail),
            "session_id": session_id,
        }
        with self._lock:
            self._ring.appendleft(entry)
        target = session_id
        if target is None and self.history is not None:
            target = self.history.current_session_id
        if self.history is not None and target:
            # Activity remains available even if a session closes while a final
            # tool event is being delivered.
            with contextlib.suppress(Exception):
                self.history.add_event(
                    target,
                    entry["kind"],
                    entry["status"],
                    entry["detail"],
                    ts=entry["ts"],
                )
        return dict(entry)

    def recent(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        with self._lock:
            return [dict(item) for item in list(self._ring)[:limit]]
