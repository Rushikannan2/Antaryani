"""Credential loading from the git-ignored ``.env`` files.

Rotating a credential in ``.env.local`` must actually change the credential
the agent uses. The entrypoint used to call ``load_dotenv(".env.local")``
with python-dotenv's default ``override=False``, so a stale
``GOOGLE_API_KEY`` left in the shell silently beat the edited file — the
classic "I changed my key and nothing happened" failure. It also never
accepted ``GEMINI_API_KEY``, the name the README documents, because the
Google plugin reads only ``os.environ["GOOGLE_API_KEY"]``.

These tests pin the loader's contract:

* a later env file beats an earlier one, and any file beats a stale
  shell value, for credential keys;
* ``GEMINI_API_KEY`` and ``GOOGLE_API_KEY`` are aliases resolved to a
  single value, with a file winning across names too;
* duplicated keys inside one file resolve to the last value;
* copy/paste whitespace and quote wrapping are stripped;
* a missing or unexpected-format key warns actionably and never logs the
  key value itself;
* non-credential keys keep standard precedence so operator overrides such
  as ``SRILATHA_DASHBOARD_AUTOSTART`` are never clobbered.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from env_config import load_environment

GOOGLE = "GOOGLE_API_KEY"
GEMINI = "GEMINI_API_KEY"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_env_local_beats_stale_shell_value(tmp_path: Path) -> None:
    """A rotated key in ``.env.local`` wins over a stale shell export."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GOOGLE_API_KEY=AQ.from_file\n")
    environ = {GOOGLE: "AIzaStaleShell"}

    load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.from_file"


def test_later_env_file_beats_earlier_one_and_stale_shell(tmp_path: Path) -> None:
    """``.env.local`` wins over ``.env`` and over a stale shell value."""
    base = tmp_path / ".env"
    local = tmp_path / ".env.local"
    _write(base, "LIVEKIT_URL=wss://base.example\n")
    _write(local, "LIVEKIT_URL=wss://local.example\n")
    environ = {"LIVEKIT_URL": "wss://stale.example"}

    load_environment(files=[base, local], environ=environ)

    assert environ["LIVEKIT_URL"] == "wss://local.example"


def test_duplicate_key_in_one_file_uses_last_value(tmp_path: Path) -> None:
    """Duplicated lines (the "key is duped" case) resolve to the last one."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GOOGLE_API_KEY=AQ.first\nGOOGLE_API_KEY=AQ.second\n")
    environ: dict[str, str] = {}

    load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.second"


def test_gemini_api_key_alias_populates_google_api_key(tmp_path: Path) -> None:
    """``GEMINI_API_KEY`` (the README's name) feeds the plugin's name."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GEMINI_API_KEY=AQ.from_gemini_name\n")
    environ: dict[str, str] = {}

    load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.from_gemini_name"
    assert environ[GEMINI] == "AQ.from_gemini_name"


def test_file_gemini_value_beats_stale_shell_google_value(tmp_path: Path) -> None:
    """A file value wins across names: new file key beats stale shell key."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GEMINI_API_KEY=AQ.rotated\n")
    environ = {GOOGLE: "AIzaStaleShell"}

    load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.rotated"


def test_conflicting_names_prefer_google_and_do_not_leak(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Both names set to different values: ``GOOGLE_API_KEY`` wins, warn, leak nothing."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GOOGLE_API_KEY=AQ.google_one\nGEMINI_API_KEY=AQ.gemini_two\n")
    environ: dict[str, str] = {}

    with caplog.at_level(logging.WARNING):
        load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.google_one"
    assert environ[GEMINI] == "AQ.google_one"
    assert "different values" in caplog.text
    assert "AQ.google_one" not in caplog.text
    assert "AQ.gemini_two" not in caplog.text


def test_credentials_are_stripped_of_whitespace_and_quotes(tmp_path: Path) -> None:
    """Pasted whitespace, newlines, and quote wrapping are cleaned up."""
    env_file = tmp_path / ".env.local"
    _write(
        env_file,
        'GOOGLE_API_KEY =  "AQ.spaced out"  \nLIVEKIT_API_SECRET = "  shhh  "\n',
    )
    environ = {GEMINI: "  'AQ.stale_shell'  "}

    load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.spacedout"
    assert environ[GEMINI] == "AQ.spacedout"
    assert environ["LIVEKIT_API_SECRET"] == "shhh"


def test_missing_google_key_warns_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No key anywhere: actionable warning, no crash at import time."""
    environ: dict[str, str] = {}

    with caplog.at_level(logging.WARNING):
        load_environment(files=[tmp_path / ".env.local"], environ=environ)

    assert GOOGLE not in environ
    assert "GOOGLE_API_KEY" in caplog.text
    assert ".env.local" in caplog.text


def test_unexpected_key_prefix_warns_but_still_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An odd-looking key is used anyway, with a warning that hides the value."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GOOGLE_API_KEY=sk-not_a_google_key\n")
    environ: dict[str, str] = {}

    with caplog.at_level(logging.WARNING):
        load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "sk-not_a_google_key"
    assert "sk-not_a_google_key" not in caplog.text
    assert "AQ." in caplog.text
    assert "AIza" in caplog.text


def test_valid_key_loads_without_warnings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A current-format key loads silently — no warning spam on every boot."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "GOOGLE_API_KEY=AQ.valid-looking_key\n")
    environ: dict[str, str] = {}

    with caplog.at_level(logging.WARNING):
        load_environment(files=[env_file], environ=environ)

    assert environ[GOOGLE] == "AQ.valid-looking_key"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_non_credential_keys_keep_shell_precedence(tmp_path: Path) -> None:
    """Operator overrides (e.g. autostart disabled by the test suite) stick."""
    env_file = tmp_path / ".env.local"
    _write(env_file, "SRILATHA_DASHBOARD_PORT=9999\n")
    environ = {"SRILATHA_DASHBOARD_PORT": "0"}

    load_environment(files=[env_file], environ=environ)

    assert environ["SRILATHA_DASHBOARD_PORT"] == "0"


def test_entrypoint_loads_credentials_through_env_config() -> None:
    """The entrypoint must use the loader, not bare ``load_dotenv``.

    ``inspect.getsource`` reads the module file, so the assertion fails (and
    documents the regression) if ``load_dotenv`` is ever reintroduced with
    its shell-wins default.
    """
    import agent

    source = inspect.getsource(agent)

    assert "load_environment(" in source
    assert "load_dotenv(" not in source, (
        "load_dotenv defaults to override=False: a stale GOOGLE_API_KEY in "
        "the shell would beat a rotated key in .env.local. Use "
        "load_environment() from env_config instead."
    )
