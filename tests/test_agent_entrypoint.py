"""Entrypoint ordering for the room connection.

The LiveKit job runner warns if the room connection is not *established*
within 10 seconds of job entry: ``JobContext.connect()`` only marks itself
complete after ``room.connect()`` succeeds, and ``AgentSession.start()``
performs a multi-second model warmup before it schedules its own internal
connect.  Connecting after ``session.start()`` therefore consumed the whole
10-second window in warmup and triggered the warning on every job (and on a
slow network the peer connection timed out before it could recover).

``JobContext.connect()`` is idempotent and ``RoomIO`` is built from the
``room_options`` passed to ``session.start()`` regardless of when the room is
connected, so the connection is established at entrypoint start — before the
warmup — with no behavioural change.
"""

from __future__ import annotations

import inspect


def test_room_connect_runs_before_session_start() -> None:
    """``ctx.connect()`` must be awaited before ``session.start(...)``.

    ``inspect.getsource`` reads the module file, so the assertion fails (and
    documents the regression) if the two calls are ever reordered.
    """
    import agent

    source = inspect.getsource(agent)

    connect_idx = source.index("await ctx.connect()")
    start_idx = source.index("await session.start(")

    assert connect_idx < start_idx, (
        "await ctx.connect() must run before await session.start(): "
        "session.start() warms up models for several seconds, so connecting "
        "after it guarantees the 10-second room-connection warning."
    )
