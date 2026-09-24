"""Local environment (.env) loading for the Srilatha agent.

The credentials the agent actually uses must always reflect the file the
operator just edited. ``load_dotenv`` defaults to ``override=False``, so a
stale ``GOOGLE_API_KEY`` left in the shell silently beats a rotated key in
``.env.local`` — the classic "I changed my key and nothing happened"
failure.

This loader fixes that with one simple precedence model:

* **Credential keys** (Google + LiveKit): the *files* win over the shell,
  and a later file (``.env.local``) wins over an earlier one (``.env``).
  Duplicated lines inside one file resolve to the last occurrence
  (python-dotenv's behaviour). Values are cleaned of copy/paste artefacts:
  wrapping quotes, leading/trailing whitespace, and any internal
  whitespace (real keys, secrets, and URLs never contain spaces — a key
  wrapped across two lines during copy/paste still works).
* **Everything else** (``SRILATHA_*`` etc.): standard precedence — the
  shell wins, then ``.env.local``, then ``.env`` — so operator and test
  overrides are never clobbered.
* ``GEMINI_API_KEY`` and ``GOOGLE_API_KEY`` are aliases: either name in
  either file (or the shell) resolves to a single key that is written to
  *both* names, because the LiveKit Google plugin only reads
  ``GOOGLE_API_KEY`` while the README documents ``GEMINI_API_KEY``.

A missing or unexpected-format key logs an actionable warning at import
time — never a hard failure (tests and tooling import this module without
credentials), and the key value itself is never logged. Google AI Studio
currently issues ``AQ.…`` auth keys; legacy ``AIza…`` keys are also
accepted.

Default file locations are resolved relative to the project root (this
file's parent directory), so the agent finds its credentials regardless of
the working directory it was started from. Callers may pass explicit
``files`` (used by the tests) and an explicit ``environ`` mapping.
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

#: Project root: this module lives in ``<root>/src``.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Loaded in order; a later file wins over an earlier one for credentials.
ENV_FILES: tuple[Path, ...] = (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local")

#: Keys whose value must reflect the env *files*, not the shell.
CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    }
)

#: The two names for the Google key, in preference order (a tie inside one
#: source is resolved in favour of the first name — ``GOOGLE_API_KEY``,
#: the only one the plugin reads).
GOOGLE_KEY_NAMES: tuple[str, ...] = ("GOOGLE_API_KEY", "GEMINI_API_KEY")

#: Google API key shapes: current AI Studio auth keys and legacy traffic
#: keys. Anything else still loads, but gets a warning.
_VALID_KEY_PREFIXES: tuple[str, ...] = ("AQ.", "AIza")


def _clean(value: str) -> str:
    """Normalise a credential pasted from a console or a text editor.

    Strips wrapping quotes and all whitespace. Whitespace is never valid
    inside the handled credentials, so removing it repairs keys that were
    line-wrapped during copy/paste without risking a legitimate value.
    """
    text = value.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return "".join(text.split())


def _read_files(paths: Sequence[Path]) -> list[dict[str, str]]:
    """Parse each existing env file, later entries per file already winning.

    Missing files are skipped (python-dotenv also returns an empty mapping
    for them); an unreadable file logs a warning without leaking contents.
    """
    snapshots: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            snapshots.append(
                {k: v for k, v in dotenv_values(path).items() if v is not None}
            )
        except OSError as exc:
            logger.warning(
                "could not read env file %s: %s", path.name, type(exc).__name__
            )
    return snapshots


def load_environment(
    files: Sequence[str | Path] | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Populate ``environ`` (default: ``os.environ``) from ``files``.

    See the module docstring for the precedence rules. Never raises: a
    missing key is reported as a warning so importing the agent without
    credentials (tests, tooling) keeps working.
    """
    env = os.environ if environ is None else environ
    paths = [Path(p) for p in (ENV_FILES if files is None else files)]

    # Snapshot shell credential values before any file value is written, so
    # the Google alias resolution can tell sources apart.
    shell_creds = {k: _clean(env[k]) for k in CREDENTIAL_KEYS if env.get(k)}
    snapshots = _read_files(paths)

    # 1) Non-credential keys: shell wins, then the later file.
    merged_files: dict[str, str] = {}
    for snapshot in snapshots:
        merged_files.update(snapshot)
    for key, value in merged_files.items():
        if key not in CREDENTIAL_KEYS and key not in env:
            env[key] = value

    # 2) Credential keys: files win over the shell; the later file wins.
    file_creds: dict[str, str] = {}
    for snapshot in snapshots:
        for key, value in snapshot.items():
            if key in CREDENTIAL_KEYS:
                cleaned = _clean(value)
                if cleaned:
                    file_creds[key] = cleaned
    for key, value in file_creds.items():
        env[key] = value

    # Also clean shell-only credentials (a stray space in a shell export
    # would otherwise survive and break auth in a confusing way).
    for key in CREDENTIAL_KEYS:
        if key not in file_creds and env.get(key):
            cleaned = _clean(env[key])
            if cleaned:
                env[key] = cleaned

    # 3) Resolve the Google alias across both names and all sources.
    #    Rank order: shell (-1) < first file (0) < later files; within one
    #    source GOOGLE_API_KEY beats GEMINI_API_KEY.
    candidates: list[tuple[int, int, str]] = []
    distinct: set[str] = set()
    for name_index, name in enumerate(GOOGLE_KEY_NAMES):
        if shell_creds.get(name):
            distinct.add(shell_creds[name])
            candidates.append((-1, -name_index, shell_creds[name]))
    for rank, snapshot in enumerate(snapshots):
        for name_index, name in enumerate(GOOGLE_KEY_NAMES):
            if name in snapshot:
                cleaned = _clean(snapshot[name])
                if cleaned:
                    distinct.add(cleaned)
                    candidates.append((rank, -name_index, cleaned))

    if candidates:
        resolved = max(candidates, key=lambda c: c[:2])[2]
        env["GOOGLE_API_KEY"] = resolved
        env["GEMINI_API_KEY"] = resolved
        if len(distinct) > 1:
            logger.warning(
                "different values found for GOOGLE_API_KEY/GEMINI_API_KEY "
                "across .env files and the environment; using the most "
                "specific file value. Key values are never logged."
            )
        if not resolved.startswith(_VALID_KEY_PREFIXES):
            logger.warning(
                "GOOGLE_API_KEY does not start with 'AQ.' or 'AIza'; it may "
                "have been pasted incompletely. Copy it again from Google AI "
                "Studio. The value itself is never logged."
            )
    else:
        logger.warning(
            "GOOGLE_API_KEY is not set: copy .env.example to .env.local and "
            "add your Google AI Studio key (new keys start with 'AQ.', legacy "
            "'AIza'). GEMINI_API_KEY is accepted as an alias."
        )
