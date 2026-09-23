"""Integration tests for BrowserManager against a local HTTP fixture site.

Requires the Playwright Chromium binary: `uv run playwright install chromium`.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

import browser as browser_mod
from browser import (
    BrowserError,
    BrowserManager,
    normalize_url,
    pick_element,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


@pytest.fixture()
def base_url() -> str:
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
async def browser(tmp_path):
    manager = BrowserManager(headless=True, screenshots_dir=tmp_path / "shots")
    try:
        yield manager
    finally:
        await manager.close()


# ----------------------------------------------------------------------
# pure helpers (no browser required)
# ----------------------------------------------------------------------
def test_normalize_url_adds_https() -> None:
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("https://example.com/a?b=1") == "https://example.com/a?b=1"
    assert normalize_url("http://example.com") == "http://example.com"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "mailto:x@example.com", "   "],
)
def test_normalize_url_rejects_non_web_schemes(url: str) -> None:
    with pytest.raises(BrowserError):
        normalize_url(url)


def test_pick_element_exact_match() -> None:
    elements = [
        {"ref": 0, "role": "button", "name": "Send message"},
        {"ref": 5, "role": "link", "name": "Documentation"},
    ]
    assert pick_element("send message", elements)["ref"] == 0
    assert pick_element("Documentation", elements)["ref"] == 5


def test_pick_element_containment_match() -> None:
    elements = [{"ref": 2, "role": "textbox", "name": "Search terms"}]
    assert pick_element("search", elements)["ref"] == 2
    assert pick_element("the search terms field", elements)["ref"] == 2


def test_pick_element_rejects_ambiguous_target() -> None:
    elements = [
        {"ref": 0, "role": "link", "name": "Docs"},
        {"ref": 1, "role": "link", "name": "Docs mirror"},
    ]
    with pytest.raises(BrowserError, match="several elements"):
        pick_element("doc", elements)
    # an exact accessible name still wins over partial matches
    assert pick_element("docs", elements)["ref"] == 0


def test_pick_element_rejects_missing_target() -> None:
    with pytest.raises(BrowserError, match="No interactive element"):
        pick_element("nonexistent", [{"ref": 0, "role": "link", "name": "Home"}])


def test_pick_element_matches_unique_role() -> None:
    elements = [
        {"ref": 0, "role": "link", "name": "Home"},
        {"ref": 1, "role": "combobox", "name": ""},
    ]
    assert pick_element("combobox", elements)["ref"] == 1


# ----------------------------------------------------------------------
# navigation and reading
# ----------------------------------------------------------------------
async def test_open_url_and_read_page(browser: BrowserManager, base_url: str) -> None:
    opened = await browser.open_url(f"{base_url}/index.html")
    assert opened["title"] == "Test Page"
    assert "Welcome to the test page" in opened["preview"]

    page = await browser.read_page()
    assert page["url"].endswith("/index.html")
    assert page["truncated"] is False
    assert "Welcome to the test page" in str(page["text"])


async def test_open_url_rejects_disallowed_scheme(browser: BrowserManager) -> None:
    with pytest.raises(BrowserError, match="Only web pages"):
        await browser.open_url("file:///etc/passwd")


async def test_inspect_page_returns_accessible_elements(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    state = await browser.inspect_page()

    named = {(el["role"], el["name"]) for el in state["elements"]}
    assert ("button", "Greeting") in named
    assert ("link", "Documentation") in named
    assert ("textbox", "Search terms") in named
    assert ("combobox", "Country") in named
    assert ("checkbox", "Remember me") in named


async def test_get_page_state_combines_text_and_elements(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    state = await browser.get_page_state()
    assert "Welcome to the test page" in str(state["text"])
    assert any(el["name"] == "Greeting" for el in state["elements"])


# ----------------------------------------------------------------------
# interaction
# ----------------------------------------------------------------------
async def test_click_reveals_content(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    await browser.click("Greeting")
    page = await browser.read_page()
    assert "You clicked the greeting button" in str(page["text"])


async def test_type_text_fills_field(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    await browser.type_text("Search terms", "hello world")
    page = await browser.read_page()
    assert "You typed hello world" in str(page["text"])


async def test_clear_field_empties_input(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    await browser.type_text("Search terms", "hello world")
    await browser.clear_field("Search terms")
    page = await browser.read_page()
    assert "You typed " not in str(page["text"])


async def test_type_text_unknown_target_raises(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    with pytest.raises(BrowserError, match="No interactive element"):
        await browser.type_text("Nope field", "value")


async def test_select_option_by_label(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    result = await browser.select_option("Country", "India")
    assert result["selected"] == "India"


async def test_select_option_rejects_unknown_value(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    with pytest.raises(BrowserError, match="Available options"):
        await browser.select_option("Country", "Atlantis")


async def test_toggle_checkbox_updates_page(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    result = await browser.toggle_checkbox("Remember me", True)
    assert result["checked"] is True
    page = await browser.read_page()
    assert "Remember me is now checked" in str(page["text"])


async def test_submit_form_without_form_element_raises(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    with pytest.raises(BrowserError, match="not inside a form"):
        await browser.submit_form("Greeting")


async def test_submit_form_succeeds_after_confirmation(
    browser: BrowserManager, base_url: str
) -> None:
    # the confirmation gate lives in BrowserTools; here the manager must be
    # able to submit the form itself
    await browser.open_url(f"{base_url}/index.html")
    result = await browser.submit_form("Send")
    assert result["url"].endswith("/index.html")
    page = await browser.read_page()
    assert "Form submitted" in str(page["text"])


async def test_hover_and_double_click(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    await browser.hover("Greeting")
    await browser.double_click("Greeting")
    page = await browser.read_page()
    assert "You clicked the greeting button" in str(page["text"])


async def test_scroll_accepts_only_up_or_down(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    assert (await browser.scroll("down"))["scrolled"] == "down"
    assert (await browser.scroll("up"))["scrolled"] == "up"
    with pytest.raises(BrowserError, match=r"'up' or 'down'"):
        await browser.scroll("sideways")


async def test_press_key_whitelist(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    assert (await browser.press_key("enter"))["key"] == "Enter"
    with pytest.raises(BrowserError, match="Only these keys are allowed"):
        await browser.press_key("A")


# ----------------------------------------------------------------------
# history, reload, waiting
# ----------------------------------------------------------------------
async def test_go_back_and_forward(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    await browser.click("Documentation")
    page = await browser.read_page()
    assert "You navigated to the second page" in str(page["text"])

    await browser.go_back()
    page = await browser.read_page()
    assert "Welcome to the test page" in str(page["text"])

    await browser.go_forward()
    page = await browser.read_page()
    assert "You navigated to the second page" in str(page["text"])


async def test_go_back_without_history_raises(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    # the first back lands on the initial about:blank entry
    await browser.go_back()
    with pytest.raises(BrowserError, match="no page to the back"):
        await browser.go_back()


async def test_refresh(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    await browser.click("Greeting")
    await browser.refresh()
    page = await browser.read_page()
    # a reload resets the DOM to its initial state
    assert "You clicked the greeting button" not in str(page["text"])


async def test_wait_for_delayed_text(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")
    state = await browser.wait_for_content("Late content arrived")
    assert "Late content arrived" in str(state["text"])


async def test_wait_for_missing_text_raises(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    with pytest.raises(BrowserError, match="did not appear"):
        await browser.wait_for_content("This text never appears")


async def test_wait_for_content_without_text_settles(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    state = await browser.wait_for_content()
    assert state["title"] == "Test Page"


# ----------------------------------------------------------------------
# tabs
# ----------------------------------------------------------------------
async def test_tab_lifecycle(browser: BrowserManager, base_url: str) -> None:
    await browser.open_url(f"{base_url}/index.html")

    opened = await browser.new_tab(f"{base_url}/second.html")
    assert opened["index"] == 1
    assert opened["title"] == "Second Page"

    listing = await browser.list_tabs()
    assert listing["current"] == 1
    assert len(listing["tabs"]) == 2

    await browser.switch_tab(0)
    page = await browser.read_page()
    assert page["title"] == "Test Page"

    closed = await browser.close_tab()
    assert closed["current"] == 0
    listing = await browser.list_tabs()
    assert len(listing["tabs"]) == 1


async def test_close_last_tab_is_rejected(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    with pytest.raises(BrowserError, match="only open tab"):
        await browser.close_tab()


async def test_switch_unknown_tab_is_rejected(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    with pytest.raises(BrowserError, match="no tab"):
        await browser.switch_tab(9)


# ----------------------------------------------------------------------
# screenshots
# ----------------------------------------------------------------------
async def test_take_screenshot_saves_jpeg(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    result = await browser.take_screenshot()
    path = Path(str(result["saved_path"]))
    assert path.exists()
    assert path.suffix == ".jpg"
    assert path.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic
    assert result["title"] == "Test Page"
    assert result["size_bytes"] > 0


# ----------------------------------------------------------------------
# lifecycle
# ----------------------------------------------------------------------
async def test_close_is_idempotent(tmp_path) -> None:
    manager = BrowserManager(headless=True, screenshots_dir=tmp_path)
    await manager.close()
    await manager.close()


async def test_reopens_after_page_closed(
    browser: BrowserManager, base_url: str
) -> None:
    await browser.open_url(f"{base_url}/index.html")
    assert browser_mod is not None  # module import sanity
    page = await browser._ensure_page()
    await page.close()
    reopened = await browser.open_url(f"{base_url}/second.html")
    assert reopened["title"] == "Second Page"
