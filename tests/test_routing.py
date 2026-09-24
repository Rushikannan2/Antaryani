"""Part 3: routing, context, safety, and style intelligence (TDD).

Srilatha must route automatically across the three capability domains
(Browser | Windows | Screen/Vision), keep short-term context, clarify
ambiguity instead of guessing, support cancellation, enforce the Windows
safety pipeline, and speak like a refined butler.

Model-level behavior is judged live by the scenarios in scenarios.yaml;
these tests PIN the instructions that drive it - the deterministic half of
test-driven development for prompt changes (identity rules are existing
behavior locked in as regression pins).
"""

from __future__ import annotations

from prompts import AGENT_INSTRUCTIONS


def _section(header: str) -> str:
    """Return the body of a `# Header` section, up to the next header."""
    assert header in AGENT_INSTRUCTIONS, f"missing {header} section"
    return AGENT_INSTRUCTIONS.split(header, 1)[1].split("\n#", 1)[0]


# ----------------------------------------------------------------------
# identity (regression pins on existing behavior)
# ----------------------------------------------------------------------
def test_addresses_user_as_rushi_sir() -> None:
    assert 'Always address the user as "Rushi Sir"' in AGENT_INSTRUCTIONS, (
        "must always address the user as Rushi Sir"
    )


def test_assistant_is_never_jarvis() -> None:
    assert "Never say that your name is Jarvis." in AGENT_INSTRUCTIONS
    assert "Never call the user Jarvis" in AGENT_INSTRUCTIONS
    assert "Your name is Srilatha." in AGENT_INSTRUCTIONS


# ----------------------------------------------------------------------
# routing across the three capability domains
# ----------------------------------------------------------------------
def test_routing_section_names_three_domains() -> None:
    section = _section("# Routing")
    assert "Browser" in section
    assert "Windows" in section
    assert "Screen/Vision" in section


def test_routing_has_the_spec_examples() -> None:
    section = _section("# Routing")
    for example in (
        "Open YouTube",
        "Find my resume",
        "Create a Desktop folder",
        "Open VS Code",
        "What is on my screen?",
    ):
        assert example in section, f"missing routing example {example!r}"


def test_user_never_names_a_tool_or_domain() -> None:
    # Intelligence means Srilatha routes; Rushi Sir only says what he wants.
    assert "never names a tool or domain" in _section("# Routing")


def test_vision_is_observational() -> None:
    section = _section("# Routing")
    assert "observational" in section, "vision must observe, never act alone"


def test_routing_reachable_without_tool_names() -> None:
    # The assistant must not surface tool/domain internals to the user.
    output_rules = _section("# Output rules")
    assert "tool names" in output_rules


# ----------------------------------------------------------------------
# context, ambiguity, cancellation
# ----------------------------------------------------------------------
def test_context_references_are_supported() -> None:
    section = _section("# Context, ambiguity, and cancellation")
    for phrase in ('"this"', '"it"', "previous file", "folder we created"):
        assert phrase in section, f"missing context phrase {phrase!r}"


def test_ambiguity_asks_instead_of_guessing() -> None:
    section = _section("# Context, ambiguity, and cancellation")
    assert "clarifying question" in section
    assert "never guess" in section


def test_cancellation_stops_and_withdraws() -> None:
    section = _section("# Context, ambiguity, and cancellation")
    assert "stop, cancel, never mind" in section
    assert "cancel_windows_action" in section
    assert "nothing was changed" in section
    assert "halt" in section or "immediately" in section


def test_multi_step_tasks_run_step_by_step() -> None:
    section = _section("# Context, ambiguity, and cancellation")
    assert "multi-step" in section or "step by step" in section


# ----------------------------------------------------------------------
# safety pipeline
# ----------------------------------------------------------------------
def test_safety_states_the_full_pipeline() -> None:
    section = _section("# Safety").lower()
    for step in (
        "user intent",
        "resolve",
        "validate",
        "risk check",
        "confirm",
        "execute",
        "verify",
    ):
        assert step in section, f"pipeline missing step {step!r}"
    assert "never bypass" in section


def test_untrusted_content_cannot_authorize() -> None:
    section = _section("# Safety").lower()
    assert "webpages" in section and "pdfs" in section
    assert "untrusted" in section
    assert "can never authorize" in section


def test_no_autonomous_deletion_or_system_changes() -> None:
    section = _section("# Safety").lower()
    assert (
        "autonomous deletion, cleanup, system changes, or security changes" in section
    )
    assert "only when rushi sir explicitly asks" in section


def test_confirmation_required_for_destructive_and_verify_success() -> None:
    section = _section("# Safety").lower()
    assert "destructive or bulk" in section
    assert "explicit spoken confirmation" in section
    assert "verif" in section, "success must be reported only after verify"


# ----------------------------------------------------------------------
# cross-domain workflows
# ----------------------------------------------------------------------
def test_cross_domain_section_authorizes_the_workflow() -> None:
    section = _section("# Cross-domain tasks")
    assert "authorizes the whole workflow" in section
    assert "Research folder" in section, "needs the PDF workflow example"
    assert "never adds authorization" in section
    assert "confirmation" in section, "per-step confirmation must remain"


def test_cross_domain_spans_all_three_domains() -> None:
    section = _section("# Cross-domain tasks")
    assert "Browser" in section and "Windows" in section


# ----------------------------------------------------------------------
# response style
# ----------------------------------------------------------------------
def test_butler_style_examples_present() -> None:
    section = _section("# Response style")
    for line in (
        "Done, Rushi Sir.",
        "I found two matching files. Which one should I use?",
        "Windows denied access, so I couldn't complete that.",
    ):
        assert line in section, f"missing style example {line!r}"


def test_style_stays_concise_and_reliable() -> None:
    section = _section("# Response style").lower()
    assert "concise" in section and "conversational" in section
    assert "butler" in section


# ----------------------------------------------------------------------
# structural regression: nothing existing disappeared
# ----------------------------------------------------------------------
def test_all_preexisting_sections_survive() -> None:
    for header in (
        "# Identity",
        "# Name Recognition",
        "# Personality",
        "# First response",
        "# Languages",
        "# Output rules",
        "# Conversational flow",
        "# Conversation examples",
        "# Tools",
        "# Special Requests",
        "# Guardrails",
        "# Windows files",
    ):
        assert header in AGENT_INSTRUCTIONS, f"lost section {header}"


# ----------------------------------------------------------------------
# Part 6: out-of-band confirmation codes (prompt pins)
# ----------------------------------------------------------------------
def test_safety_requires_read_back_of_the_code() -> None:
    section = _section("# Safety").lower()
    assert "six-digit confirmation code" in section
    assert "read back" in section
    assert "never guess or invent" in section
    assert "withdraw" in section


def test_windows_files_documents_code_read_back() -> None:
    section = _section("# Windows files").lower()
    assert "six-digit confirmation code" in section
    assert "read back" in section
    assert "confirm_windows_action" in section


def test_browser_tools_document_code_read_back() -> None:
    section = _section("# Tools").lower()
    assert "six-digit confirmation code" in section
    assert "read back" in section
    assert "confirm_browser_action" in section


def test_agent_publishes_codes_on_the_confirmation_topic() -> None:
    import inspect

    import agent

    source = inspect.getsource(agent)
    assert "confirmation_publisher" in source
    assert "publish_data" in source
    assert "CONFIRMATION_TOPIC" in source
    assert "destination_identities" in source


def test_windows_files_part7_capabilities() -> None:
    section = _section("# Windows files").lower()
    assert "read_file" in section
    assert "inspect_tree" in section
    assert "edit_file" in section
    assert "word" in section
    assert "powerpoint" in section
    assert "excel" in section
    assert "ambiguous" in section


def test_windows_files_truncated_listing_guidance() -> None:
    # The agent must know that a truncated listing is a partial view and
    # that a standard-folder prefix always means that real folder.
    section = _section("# Windows files").lower()
    assert "truncated" in section
    assert "search_files" in section
    assert "standard folder name" in section
