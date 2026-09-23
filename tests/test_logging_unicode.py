"""Windows log streams must survive multilingual transcripts.

The agent logs every conversation item at DEBUG level. On Windows the
standard streams default to the locale codepage (cp1252), which cannot
encode scripts like Devanagari or Chinese. Logging then raises
UnicodeEncodeError ("charmap codec can't encode characters"), the record
is lost, and Python prints a ``--- Logging error ---`` traceback.

``agent.configure_unicode_logs`` forces UTF-8 with ``errors="replace"``
on every stream logging can write to; these tests pin that behavior.
"""

from __future__ import annotations

import io
import logging
import sys

import pytest

from agent import configure_unicode_logs

_STD_STREAM_NAMES = ("stdout", "stderr", "__stdout__", "__stderr__")


def test_standard_streams_are_utf8_with_safe_errors() -> None:
    # Importing `agent` runs configure_unicode_logs() at module load.
    checked = 0
    for name in _STD_STREAM_NAMES:
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue  # pythonw-style streams or non-text streams: skip
        checked += 1
        encoding = (stream.encoding or "").lower().replace("_", "-")
        assert encoding == "utf-8", (
            f"sys.{name} encodes as {stream.encoding!r}; "
            "non-Latin log text will crash with UnicodeEncodeError"
        )
        assert stream.errors == "replace", (
            f"sys.{name} uses errors={stream.errors!r}; "
            "'replace' keeps logging alive even for odd characters"
        )
    assert checked >= 2, "expected at least stdout and stderr to be checked"


def test_cp1252_is_the_original_failure_mode() -> None:
    # Reproduce the original crash: the Windows locale codepage cannot
    # encode a Hindi transcript.
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        stream.write("नमस्ते, ऋषि सर")


def test_non_latin_log_records_survive_the_fix() -> None:
    # The fix, applied to the very stream type that used to crash.
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    configure_unicode_logs(streams=(stream,))
    configure_unicode_logs(streams=(stream,))  # must be idempotent
    encoding = (stream.encoding or "").lower().replace("_", "-")
    assert encoding == "utf-8"
    assert stream.errors == "replace"

    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("srilatha.unicode-test")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.debug("conversation_item_added: %s", "नमस्ते, ऋषि सर")
    finally:
        logger.removeHandler(handler)

    written = stream.buffer.getvalue().decode("utf-8")
    assert "नमस्ते, ऋषि सर" in written
