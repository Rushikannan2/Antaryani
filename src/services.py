"""Process-wide service singletons shared by the agent, tools, and API.

One source of truth: the dashboard, the model tools, and the session
lifecycle all talk to the same history, activity, and recorder objects.
"""

from __future__ import annotations

import threading

from activity import ActivityLog
from screen_recording import RecordingManager
from session_history import SessionHistory

_lock = threading.RLock()
_history: SessionHistory | None = None
_activity: ActivityLog | None = None
_recorder: RecordingManager | None = None


def get_history() -> SessionHistory:
    global _history
    with _lock:
        if _history is None:
            _history = SessionHistory()
        return _history


def get_activity() -> ActivityLog:
    global _activity
    with _lock:
        if _activity is None:
            _activity = ActivityLog(history=get_history())
        return _activity


def get_recorder() -> RecordingManager:
    global _recorder
    with _lock:
        if _recorder is None:
            _recorder = RecordingManager()
        return _recorder
