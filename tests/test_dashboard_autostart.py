"""The dashboard API must serve from worker boot, before the first job.

``lk agent dev``/``lk agent start`` import ``src/agent.py`` through
``importlib`` instead of running it as ``__main__``, so the old
``if __name__ == "__main__"`` thread never started. The API only came up
when the first job arrived, and the frontend proxy hit ECONNREFUSED until
then. Autostart now lives at import time, is env-gated so tests never bind
the port, and falls back to the per-job bind if the boot bind fails.
"""

from __future__ import annotations

import importlib
import os
import sys
import time

import dashboard_api

_ENV = "SRILATHA_DASHBOARD_AUTOSTART"


class _StubDashboardServices:
    """Stand-in so the autostart test never touches the real session DB."""

    def app(self) -> object:
        return object()


def _reload_agent_with_stub(enabled: str | None, *, expect_started: bool) -> list:
    """Reload ``agent`` under a stubbed dashboard; return captured apps.

    Assertions run while the stubs are installed so the autostart thread can
    never call the real ``run_server`` mid-test.
    """
    calls: list = []
    original_services = dashboard_api.DashboardServices
    original_run_server = dashboard_api.run_server

    def _stub_run_server(app=None, **_kwargs) -> None:
        calls.append(app)

    dashboard_api.DashboardServices = _StubDashboardServices
    dashboard_api.run_server = _stub_run_server
    if enabled is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = enabled
    try:
        # Exactly one module execution under the stubs, so its autostart
        # thread is always awaited before the real functions return.
        if "agent" in sys.modules:
            importlib.reload(sys.modules["agent"])
        else:
            importlib.import_module("agent")
        if expect_started:
            deadline = time.monotonic() + 5.0
            while not calls and time.monotonic() < deadline:
                time.sleep(0.05)
            assert calls, "importing agent must boot the dashboard API thread"
        else:
            time.sleep(0.3)
            assert not calls, f"{_ENV}={enabled} must not start the API"
    finally:
        # Restore the disabled test-suite state before other tests import agent.
        os.environ[_ENV] = "0"
        dashboard_api.DashboardServices = original_services
        dashboard_api.run_server = original_run_server
        module = sys.modules.get("agent")
        if module is not None:
            importlib.reload(module)
    return calls


def test_dashboard_autostarts_when_worker_imports_agent() -> None:
    calls = _reload_agent_with_stub(None, expect_started=True)
    assert len(calls) >= 1


def test_dashboard_autostart_honors_disabled_env() -> None:
    _reload_agent_with_stub("0", expect_started=False)
