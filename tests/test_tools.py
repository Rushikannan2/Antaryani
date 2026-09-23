"""Unit tests for BrowserTools: error mapping, confirmation gate, vision.

These run against a fake BrowserManager, so no browser is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.agents.llm import ImageContent, ToolError

from browser import BrowserError
from tools import BrowserTools, duckduckgo_search_url


class FakeBrowser:
    """Records calls; optionally raises BrowserError for every operation."""

    def __init__(self, error: BrowserError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, tuple, dict]] = []
        self.screenshot_result: dict = {
            "saved_path": "",
            "url": "https://example.com/",
            "title": "Example",
            "size_bytes": 0,
        }

    def __getattr__(self, name: str):
        async def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.error is not None:
                raise self.error
            if name == "take_screenshot":
                return dict(self.screenshot_result)
            return {"url": "https://example.com/", "title": "Example"}

        return method

    @property
    def called(self) -> list[str]:
        return [name for name, _, _ in self.calls]


class FakeChatCtx:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list]] = []

    def copy(self) -> FakeChatCtx:
        return FakeChatCtx()

    def add_message(self, role: str, content: list) -> None:
        self.messages.append((role, content))


class FakeAgent:
    def __init__(self, fail: bool = False) -> None:
        self.chat_ctx = FakeChatCtx()
        self.updated: list[FakeChatCtx] = []
        self.fail = fail

    async def update_chat_ctx(self, chat_ctx: FakeChatCtx) -> None:
        if self.fail:
            raise RuntimeError("context update failed")
        self.updated.append(chat_ctx)


def make_context(agent: FakeAgent | None = None) -> SimpleNamespace:
    agent = agent or FakeAgent()
    return SimpleNamespace(session=SimpleNamespace(current_agent=agent))


# ----------------------------------------------------------------------
# search URL helper
# ----------------------------------------------------------------------
def test_duckduckgo_search_url_builds_query() -> None:
    url = duckduckgo_search_url("current weather in Paris")
    assert url.startswith("https://duckduckgo.com/?")
    assert "q=current+weather+in+Paris" in url


def test_duckduckgo_search_url_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        duckduckgo_search_url("   ")


# ----------------------------------------------------------------------
# error mapping
# ----------------------------------------------------------------------
async def test_browser_error_becomes_tool_error() -> None:
    tools = BrowserTools(FakeBrowser(error=BrowserError("navigation failed")))
    with pytest.raises(ToolError, match="navigation failed"):
        await tools.open_url(make_context(), url="https://example.com")


async def test_search_wraps_value_error_as_tool_error() -> None:
    tools = BrowserTools(FakeBrowser())
    with pytest.raises(ToolError, match="cannot be empty"):
        await tools.search_the_web(make_context(), query="   ")


# ----------------------------------------------------------------------
# confirmation gate
# ----------------------------------------------------------------------
async def test_risky_click_requires_confirmation_then_passes_once() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)

    with pytest.raises(ToolError, match="confirm_browser_action"):
        await tools.click(make_context(), target="Send message")
    assert fake.called == []  # nothing executed before confirmation

    await tools.confirm_browser_action(make_context(), target="Send message")
    await tools.click(make_context(), target="Send message")
    assert fake.called == ["click"]

    # the confirmation is consumed: the same risky click is gated again
    with pytest.raises(ToolError, match="confirm_browser_action"):
        await tools.click(make_context(), target="Send message")
    assert fake.called == ["click"]


async def test_benign_click_is_not_gated() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)
    await tools.click(make_context(), target="Documentation")
    assert fake.called == ["click"]


async def test_confirmation_is_case_and_whitespace_insensitive() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)
    with pytest.raises(ToolError):
        await tools.click(make_context(), target="Send message")
    await tools.confirm_browser_action(make_context(), target="  send   MESSAGE ")
    await tools.click(make_context(), target="Send message")
    assert fake.called == ["click"]


async def test_submit_form_always_requires_confirmation() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)

    # even a benign-looking form control is gated
    with pytest.raises(ToolError, match="confirm_browser_action"):
        await tools.submit_form(make_context(), target="Search")

    await tools.confirm_browser_action(make_context(), target="Search")
    await tools.submit_form(make_context(), target="Search")
    assert fake.called == ["submit_form"]


async def test_risky_select_option_is_gated() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)

    with pytest.raises(ToolError, match="confirm_browser_action"):
        await tools.select_option(make_context(), target="Action", value="delete all")

    await tools.confirm_browser_action(make_context(), target="Action: delete all")
    await tools.select_option(make_context(), target="Action", value="delete all")
    assert fake.called == ["select_option"]


async def test_benign_select_option_passes() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)
    await tools.select_option(make_context(), target="Country", value="India")
    assert fake.called == ["select_option"]


async def test_risky_checkbox_is_gated_and_benign_passes() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)

    with pytest.raises(ToolError, match="confirm_browser_action"):
        await tools.toggle_checkbox(make_context(), target="I agree to the terms")

    await tools.toggle_checkbox(make_context(), target="Remember me")
    assert fake.called == ["toggle_checkbox"]


async def test_hover_is_not_gated() -> None:
    fake = FakeBrowser()
    tools = BrowserTools(fake)
    await tools.hover(make_context(), target="Menu")
    assert fake.called == ["hover"]


# ----------------------------------------------------------------------
# take_screenshot vision
# ----------------------------------------------------------------------
async def test_take_screenshot_adds_image_to_chat_context(tmp_path) -> None:
    image = tmp_path / "shot.jpg"
    image.write_bytes(b"\xff\xd8fakejpegbytes")

    fake = FakeBrowser()
    fake.screenshot_result = {
        "saved_path": str(image),
        "url": "https://example.com/",
        "title": "Example",
        "size_bytes": image.stat().st_size,
    }
    agent = FakeAgent()
    tools = BrowserTools(fake)

    result = await tools.take_screenshot(make_context(agent))

    assert result["saved_path"] == str(image)
    assert result["added_to_context"] is True

    assert len(agent.updated) == 1
    role, content = agent.updated[0].messages[0]
    assert role == "user"
    images = [c for c in content if isinstance(c, ImageContent)]
    assert len(images) == 1
    assert images[0].image.startswith("data:image/jpeg;base64,")


async def test_take_screenshot_survives_context_failure(tmp_path) -> None:
    image = tmp_path / "shot.jpg"
    image.write_bytes(b"\xff\xd8fakejpegbytes")

    fake = FakeBrowser()
    fake.screenshot_result = {
        "saved_path": str(image),
        "url": "https://example.com/",
        "title": "Example",
        "size_bytes": image.stat().st_size,
    }
    tools = BrowserTools(fake)

    result = await tools.take_screenshot(make_context(FakeAgent(fail=True)))

    assert result["saved_path"] == str(image)
    assert result["added_to_context"] is False


async def test_take_screenshot_maps_browser_error() -> None:
    tools = BrowserTools(FakeBrowser(error=BrowserError("capture failed")))
    with pytest.raises(ToolError, match="capture failed"):
        await tools.take_screenshot(make_context())


# ----------------------------------------------------------------------
# tool wiring
# ----------------------------------------------------------------------
EXPECTED_TOOL_NAMES = {
    "open_url",
    "search_the_web",
    "read_page",
    "inspect_page",
    "get_page_state",
    "wait_for_content",
    "go_back",
    "go_forward",
    "refresh",
    "take_screenshot",
    "click",
    "double_click",
    "hover",
    "confirm_browser_action",
    "type_text",
    "clear_field",
    "select_option",
    "toggle_checkbox",
    "submit_form",
    "scroll",
    "press_key",
    "list_tabs",
    "new_tab",
    "switch_tab",
    "close_tab",
}


def test_tools_property_exposes_every_expected_tool() -> None:
    tools = BrowserTools(FakeBrowser())
    names = {tool.id for tool in tools.tools}
    assert names == EXPECTED_TOOL_NAMES
