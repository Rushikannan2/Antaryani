"""Shared test setup.

Importing :mod:`agent` boots the localhost dashboard API thread so a running
worker serves the dashboard from process start. Tests must never bind port
8787 or touch the real session database, so the autostart default is disabled
for the whole suite; the autostart tests re-enable it explicitly with a stubbed
server.
"""

from __future__ import annotations

import os

os.environ.setdefault("SRILATHA_DASHBOARD_AUTOSTART", "0")
