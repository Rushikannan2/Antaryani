"""Shared primitives for out-of-band confirmation codes (Part 6).

The LLM must never be able to self-confirm a staged action. Every staged
confirmation carries a six-digit code generated here, in Python, at stage
time, delivered ONLY out of band:

  * the operator console (a structured ``logger.info`` line), and
  * the genuine user's frontend screen via a targeted room data message
    on ``CONFIRMATION_TOPIC``.

Model-facing tool results and prompts never contain the code, so a model
can only confirm an action by reading back the code the human was shown.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable

logger = logging.getLogger(__name__)

CONFIRMATION_TTL_SECONDS = 120.0
MAX_CODE_ATTEMPTS = 3
CONFIRMATION_TOPIC = "srilatha.confirmation"


def new_confirmation_code() -> str:
    """A random six-digit code, always zero-padded (e.g. ``"012345"``)."""
    return f"{secrets.randbelow(10**6):06d}"


def code_matches(expected: str, supplied: str) -> bool:
    """Exact code equality after stripping voice punctuation and spaces.

    Leading zeros are insignificant (``"012345"`` matches ``"12345"``)
    because speech recognition routinely drops them; everything else must
    match exactly. Empty or digitless input never matches.
    """
    if not isinstance(expected, str) or not isinstance(supplied, str):
        return False
    expected_digits = "".join(ch for ch in expected if "0" <= ch <= "9")
    supplied_digits = "".join(ch for ch in supplied if "0" <= ch <= "9")
    if not expected_digits or not supplied_digits:
        return False
    return int(expected_digits) == int(supplied_digits)


def announce_confirmation(
    publisher: Callable[[dict], None] | None,
    *,
    token: str,
    code: str,
    description: str,
    operation: str,
    source: str | None,
    destination: str | None,
    expires_in: float,
) -> None:
    """Deliver a staged code strictly out of band; never raises.

    The operator console always receives the code; ``publisher`` (the
    session's room data channel) receives the structured payload for the
    user's screen. A missing or failing publisher must never break staging.
    """
    payload = {
        "type": "confirmation",
        "token": token,
        "code": code,
        "description": description,
        "operation": operation,
        "source": source,
        "destination": destination,
        "expires_in": expires_in,
    }
    logger.info("confirmation %s code %s for %s", token, code, description)
    if publisher is None:
        return
    try:
        publisher(payload)
    except Exception:
        logger.warning("confirmation publisher failed", exc_info=True)
