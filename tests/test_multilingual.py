"""Multilingual behavior: understand and reply in the user's language.

Gemini realtime understands and speaks 99 languages (all major Indian
languages plus world languages). The prompt decides which language Srilatha
replies in, and the model config must not pin speech output to one language.
These tests pin both.
"""

from __future__ import annotations

import pytest

from agent import Assistant
from prompts import AGENT_INSTRUCTIONS


def _section(header: str) -> str:
    """Return the body of a `# Header` section, up to the next header."""
    assert header in AGENT_INSTRUCTIONS, f"missing {header} section"
    return AGENT_INSTRUCTIONS.split(header, 1)[1].split("\n#", 1)[0]


def _languages_section() -> str:
    return _section("# Languages")


def test_detects_user_language_and_replies_in_it() -> None:
    section = _languages_section().lower()
    assert "detect" in section, "must instruct detection of the user's language"
    assert "same language" in section, "must reply in the user's language"


def test_supports_major_indian_languages() -> None:
    section = _languages_section()
    for language in (
        "Hindi",
        "Tamil",
        "Telugu",
        "Bengali",
        "Marathi",
        "Kannada",
        "Malayalam",
        "Gujarati",
    ):
        assert language in section, f"missing {language}"


def test_supports_foreign_languages() -> None:
    section = _languages_section()
    for language in ("Spanish", "French", "German", "Japanese", "Chinese", "Arabic"):
        assert language in section, f"missing {language}"


def test_follows_mixed_language_speech() -> None:
    # A user mixing Hindi and English must not force a full switch.
    assert "mix" in _languages_section().lower()


def test_address_forms_survive_language_switch() -> None:
    section = _languages_section()
    assert "Rushi Sir" in section, "addressing rules must hold in every language"
    assert "Srilatha" in section, "name recognition must hold in every language"


def test_exact_replies_are_scoped_to_english_phrases() -> None:
    section = _languages_section().lower()
    assert "exact" in section, "exact-reply rules must be scoped by language"
    assert "english" in section


def test_first_greeting_offers_any_language() -> None:
    first = _section("# First response")
    assert "any language" in first, "greeting must mention any-language support"


@pytest.fixture(scope="module")
def assistant() -> Assistant:
    return Assistant()


def test_realtime_model_language_not_pinned(assistant: Assistant) -> None:
    # language="en-GB" would force every spoken reply into British English.
    language = assistant._llm._opts.language
    assert not isinstance(language, str), (
        f"speech output is pinned to {language!r}; it must be left to auto-detect"
    )


def test_end_call_farewell_follows_conversation_language(
    assistant: Assistant,
) -> None:
    farewell = assistant._end_call_tool._end_instructions
    assert farewell is not None
    assert "British-English" not in farewell
    assert "language" in farewell.lower(), (
        "farewell must follow the language of the conversation"
    )
