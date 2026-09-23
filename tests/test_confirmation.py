"""Part 6: out-of-band confirmation codes (TDD).

The LLM must not be able to self-confirm. A six-digit code is generated in
Python at stage time and delivered ONLY out of band (operator console log +
the genuine user's frontend via a targeted room data message). Model-facing
tool results never contain it, and confirm requires the exact code plus the
existing token/payload binding, TTL, and single-use guarantees.

This file pins the shared primitives; test_windows_security.py,
test_windows_tools.py, and test_tools.py pin the two gates that use them.
"""

from __future__ import annotations

from confirmation import (
    CONFIRMATION_TOPIC,
    CONFIRMATION_TTL_SECONDS,
    MAX_CODE_ATTEMPTS,
    announce_confirmation,
    code_matches,
    new_confirmation_code,
)


# ----------------------------------------------------------------------
# code generation
# ----------------------------------------------------------------------
def test_codes_are_six_digits() -> None:
    for _ in range(50):
        code = new_confirmation_code()
        assert len(code) == 6, f"code must be six characters: {code!r}"
        assert code.isdigit(), f"code must be digits: {code!r}"


def test_codes_are_zero_padded(monkeypatch) -> None:
    # the smallest possible draw must still be six characters wide
    monkeypatch.setattr("confirmation.secrets.randbelow", lambda n: 0)
    assert new_confirmation_code() == "000000"
    monkeypatch.setattr("confirmation.secrets.randbelow", lambda n: n - 1)
    assert new_confirmation_code() == "999999"


def test_attempt_limit_is_small() -> None:
    assert MAX_CODE_ATTEMPTS == 3


# ----------------------------------------------------------------------
# matching (voice-friendly but exact)
# ----------------------------------------------------------------------
def test_exact_code_matches() -> None:
    assert code_matches("482913", "482913")


def test_spaced_and_punctuated_speech_matches() -> None:
    assert code_matches("482913", "482 913")
    assert code_matches("482913", "4-8-2-9-1-3")
    assert code_matches("482913", "the code is 482913")


def test_leading_zero_variance_matches() -> None:
    # Gemini may drop the leading zero when relaying "012345" aloud
    assert code_matches("012345", "12345")
    assert code_matches("000000", "0")


def test_wrong_code_never_matches() -> None:
    assert not code_matches("482913", "482914")
    assert not code_matches("482913", "4829130")
    assert not code_matches("482913", "4829")


def test_empty_or_digitless_code_never_matches() -> None:
    assert not code_matches("482913", "")
    assert not code_matches("482913", "confirm now")
    assert not code_matches("482913", " ")


# ----------------------------------------------------------------------
# out-of-band announcement
# ----------------------------------------------------------------------
def test_announce_sends_code_to_the_publisher_only() -> None:
    sent: list[dict] = []
    announce_confirmation(
        sent.append,
        token="tok-1",
        code="482913",
        description="delete doomed.txt",
        operation="delete_path",
        source=r"C:\x\doomed.txt",
        destination=None,
        expires_in=CONFIRMATION_TTL_SECONDS,
    )
    assert len(sent) == 1
    payload = sent[0]
    assert payload["type"] == "confirmation"
    assert payload["token"] == "tok-1"
    assert payload["code"] == "482913"
    assert payload["description"] == "delete doomed.txt"
    assert payload["expires_in"] == CONFIRMATION_TTL_SECONDS


def test_announce_survives_a_failing_publisher() -> None:
    def boom(payload: dict) -> None:
        raise RuntimeError("room gone")

    # staging must never break because delivery failed
    announce_confirmation(
        boom,
        token="tok-2",
        code="111111",
        description="d",
        operation="delete_path",
        source=None,
        destination=None,
        expires_in=30,
    )


def test_announce_without_publisher_is_safe() -> None:
    announce_confirmation(
        None,
        token="tok-3",
        code="222222",
        description="d",
        operation="delete_path",
        source=None,
        destination=None,
        expires_in=30,
    )


def test_topic_is_shared_with_the_frontend_banner() -> None:
    # frontend/components/app/confirmation-banner.tsx listens on this topic
    assert CONFIRMATION_TOPIC == "srilatha.confirmation"
