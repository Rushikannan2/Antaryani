import base64
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ImageContent, ToolError

from browser import BrowserError, BrowserManager
from confirmation import (
    CONFIRMATION_TTL_SECONDS,
    MAX_CODE_ATTEMPTS,
    announce_confirmation,
    code_matches,
    new_confirmation_code,
)

logger = logging.getLogger(__name__)

# Words and phrases that mark a browser control as consequential. Targets
# containing these require an explicit confirm_browser_action first.
RISKY_WORDS = {
    "accept",
    "agree",
    "buy",
    "checkout",
    "confirm",
    "delete",
    "discard",
    "pay",
    "purchase",
    "remove",
    "send",
    "submit",
    "transfer",
}
RISKY_PHRASES = (
    "accept all",
    "book now",
    "clear all",
    "delete all",
    "i agree",
    "order now",
    "pay now",
    "place order",
)


def duckduckgo_search_url(query: str) -> str:
    query = query.strip()
    if not query:
        raise ValueError("The search query cannot be empty.")
    return f"https://duckduckgo.com/?{urlencode({'q': query})}"


def _normalize_label(label: str) -> str:
    return " ".join(label.casefold().split())


@dataclass
class _BrowserPending:
    """A staged browser action awaiting genuine user confirmation."""

    token: str
    label: str  # normalized target label
    code: str
    expires_at: float  # time.monotonic() deadline
    attempts: int = 0


class BrowserTools:
    def __init__(
        self,
        browser: BrowserManager,
        confirmation_publisher: Callable[[dict], None] | None = None,
        confirmation_ttl: float = CONFIRMATION_TTL_SECONDS,
    ) -> None:
        self.browser = browser
        self._confirmation_publisher = confirmation_publisher
        self._confirmation_ttl = confirmation_ttl
        self._pending_browser: dict[str, _BrowserPending] = {}
        self._confirmed_label: str | None = None

    @property
    def tools(self) -> list:
        return [
            self.open_url,
            self.search_the_web,
            self.read_page,
            self.inspect_page,
            self.get_page_state,
            self.wait_for_content,
            self.go_back,
            self.go_forward,
            self.refresh,
            self.take_screenshot,
            self.click,
            self.double_click,
            self.hover,
            self.confirm_browser_action,
            self.type_text,
            self.clear_field,
            self.select_option,
            self.toggle_checkbox,
            self.submit_form,
            self.scroll,
            self.press_key,
            self.list_tabs,
            self.new_tab,
            self.switch_tab,
            self.close_tab,
        ]

    # ------------------------------------------------------------------
    # confirmation gate
    # ------------------------------------------------------------------
    @staticmethod
    def _requires_confirmation(target: str) -> bool:
        lowered = target.casefold()
        if any(phrase in lowered for phrase in RISKY_PHRASES):
            return True
        return bool(RISKY_WORDS.intersection(lowered.split()))

    def _require_confirmation(
        self, label: str, purpose: str, force: bool = False
    ) -> None:
        if not force and not self._requires_confirmation(label):
            return
        normalized = _normalize_label(label)
        if self._confirmed_label == normalized:
            self._confirmed_label = None
            return
        # Stage a single-use pending confirmation bound to this exact label.
        # An active staging for the same label is reused so its code stays
        # stable across retries; expired entries are dropped.
        now = time.monotonic()
        pending: _BrowserPending | None = None
        for token in list(self._pending_browser):
            candidate = self._pending_browser[token]
            if now >= candidate.expires_at:
                del self._pending_browser[token]
                continue
            if candidate.label == normalized:
                pending = candidate
        if pending is None:
            pending = _BrowserPending(
                token=secrets.token_urlsafe(16),
                label=normalized,
                code=new_confirmation_code(),
                expires_at=now + self._confirmation_ttl,
            )
            self._pending_browser[pending.token] = pending
        announce_confirmation(
            self._confirmation_publisher,
            token=pending.token,
            code=pending.code,
            description=f"{purpose} {label}",
            operation="browser_action",
            source=label,
            destination=None,
            expires_in=self._confirmation_ttl,
        )
        raise ToolError(
            f"Staged for user confirmation: token '{pending.token}'. "
            f"This action may be consequential. Explain what {purpose} "
            f"{label!r} will do and ask the user to confirm. The six-digit "
            "confirmation code is shown to the user only, never to you; he "
            "must read back that exact code to you. Call "
            f"confirm_browser_action with this token, that code, and target "
            f"{label!r} before retrying."
        )

    # ------------------------------------------------------------------
    # navigation and reading
    # ------------------------------------------------------------------
    @function_tool()
    async def search_the_web(
        self,
        context: RunContext,
        query: str,
    ) -> dict[str, str]:
        """Open fallback DuckDuckGo results in the agent-controlled browser.

        Use this only when the user needs a general internet search and did not name a
        website, service, or domain. If the user names a destination, open its official
        URL directly with open_url instead. Read or inspect the resulting page before
        answering the user.

        Args:
            query: A concise DuckDuckGo search query containing all relevant context.
        """
        try:
            return await self.browser.open_url(duckduckgo_search_url(query))
        except (BrowserError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def open_url(self, context: RunContext, url: str) -> dict[str, str]:
        """Open a public webpage directly in the agent-controlled browser.

        Prefer this over DuckDuckGo whenever the user names a website, service, domain,
        or specific destination. Use the destination's official URL.

        Args:
            url: A complete http or https URL to open.
        """
        try:
            return await self.browser.open_url(url)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def read_page(self, context: RunContext) -> dict[str, str | bool]:
        """Read the visible text from the current browser page."""
        try:
            return await self.browser.read_page()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def inspect_page(self, context: RunContext) -> dict[str, object]:
        """Inspect the current page, including readable text and interactive element names.

        Use this before clicking or typing so you can choose a visible control by its
        returned name or role.
        """
        try:
            return await self.browser.inspect_page()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def get_page_state(self, context: RunContext) -> dict[str, object]:
        """Get the current page's address, title, visible text, and interactive elements in one call.

        Prefer this after opening or reloading a page: it replaces separate
        read_page and inspect_page calls.
        """
        try:
            return await self.browser.get_page_state()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def wait_for_content(
        self, context: RunContext, text: str = ""
    ) -> dict[str, object]:
        """Wait for the current page to finish loading, or for specific text to appear on it.

        Use when a page looks half-loaded or expected content is missing. Returns the
        full page state once the wait completes.

        Args:
            text: Optional text to wait for. Omit to simply wait for the page to settle.
        """
        try:
            return await self.browser.wait_for_content(text or None)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def go_back(self, context: RunContext) -> dict[str, str]:
        """Go back to the previous page in the agent-controlled browser."""
        try:
            return await self.browser.go_back()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def go_forward(self, context: RunContext) -> dict[str, str]:
        """Go forward to the next page in the current tab's history."""
        try:
            return await self.browser.go_forward()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def refresh(self, context: RunContext) -> dict[str, str]:
        """Reload the current browser page."""
        try:
            return await self.browser.refresh()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    # ------------------------------------------------------------------
    # visual
    # ------------------------------------------------------------------
    @function_tool()
    async def take_screenshot(self, context: RunContext) -> dict[str, str | int | bool]:
        """Capture the current browser page as an image and add it to your context.

        The screenshot lets you see the page visually. Use it when text inspection is
        not enough, such as for layout, images, or charts. Do not use it routinely.
        """
        try:
            result = await self.browser.take_screenshot()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc
        added = await self._add_image_to_context(context, str(result["saved_path"]))
        result["added_to_context"] = added
        return result

    async def _add_image_to_context(self, context: RunContext, path: str) -> bool:
        """Best-effort: add the screenshot to the model's chat context as an image."""
        try:
            payload = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            agent = context.session.current_agent
            chat_ctx = agent.chat_ctx.copy()
            chat_ctx.add_message(
                role="user",
                content=[
                    "[Screenshot of the web page currently open in the agent's browser.]",
                    ImageContent(image=f"data:image/jpeg;base64,{payload}"),
                ],
            )
            await agent.update_chat_ctx(chat_ctx)
            return True
        except Exception:
            logger.warning(
                "failed to add screenshot to the chat context", exc_info=True
            )
            return False

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------
    @function_tool()
    async def click(self, context: RunContext, target: str) -> dict[str, str]:
        """Click a visible control by its accessible name.

        Args:
            target: The visible or accessible name of the control to click.
        """
        self._require_confirmation(target, "clicking")
        try:
            return await self.browser.click(target)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def double_click(self, context: RunContext, target: str) -> dict[str, str]:
        """Double-click a visible control by its accessible name.

        Args:
            target: The visible or accessible name of the control to double-click.
        """
        self._require_confirmation(target, "double-clicking")
        try:
            return await self.browser.double_click(target)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def hover(self, context: RunContext, target: str) -> dict[str, str]:
        """Move the pointer over a visible control, which may reveal a menu or tooltip.

        Args:
            target: The visible or accessible name of the control to hover over.
        """
        try:
            return await self.browser.hover(target)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def confirm_browser_action(
        self, context: RunContext, target: str, token: str, code: str
    ) -> str:
        """Authorize one previously discussed consequential browser action.

        Call this only after the user explicitly confirms the exact action
        AND reads back the six-digit confirmation code shown only to him.

        Args:
            target: The exact target wording the user approved.
            token: The single-use token from the staged message.
            code: The six-digit confirmation code the user read back.
        """
        pending = self._pending_browser.get(token)
        if pending is None:
            raise ToolError(
                "No pending confirmation matches that token; it may have "
                "expired, been used, or been cancelled."
            )
        if time.monotonic() >= pending.expires_at:
            del self._pending_browser[token]
            raise ToolError(
                "The staged confirmation has expired; ask the user to confirm again."
            )
        if not code_matches(pending.code, code):
            pending.attempts += 1
            if pending.attempts >= MAX_CODE_ATTEMPTS:
                del self._pending_browser[token]
                raise ToolError(
                    "Too many wrong confirmation codes; the staged action "
                    "has been withdrawn."
                )
            raise ToolError(
                "The confirmation code does not match the code shown to the user."
            )
        if _normalize_label(target) != pending.label:
            raise ToolError(
                "The confirmation does not match the staged action; it was "
                "staged for a different target."
            )
        del self._pending_browser[token]  # single use
        self._confirmed_label = pending.label
        return f"The user confirmed {target!r}."

    @function_tool()
    async def type_text(
        self,
        context: RunContext,
        target: str,
        text: str,
    ) -> dict[str, str]:
        """Fill a visible text field by its label, placeholder, or accessible name.

        Args:
            target: The label, placeholder, or accessible name of the text field.
            text: The text to enter.
        """
        try:
            return await self.browser.type_text(target, text)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def clear_field(self, context: RunContext, target: str) -> dict[str, str]:
        """Empty a visible text field by its label, placeholder, or accessible name.

        Args:
            target: The label, placeholder, or accessible name of the text field.
        """
        try:
            return await self.browser.clear_field(target)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def select_option(
        self,
        context: RunContext,
        target: str,
        value: str,
    ) -> dict[str, str]:
        """Choose an option in a dropdown list by the dropdown's name and the option's visible text.

        Args:
            target: The accessible name of the dropdown list.
            value: The visible text (or value) of the option to select.
        """
        self._require_confirmation(f"{target}: {value}", "selecting")
        try:
            return await self.browser.select_option(target, value)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def toggle_checkbox(
        self,
        context: RunContext,
        target: str,
        checked: bool = True,
    ) -> dict[str, str]:
        """Check or uncheck a checkbox or switch by its accessible name.

        Args:
            target: The accessible name of the checkbox or switch.
            checked: True to check it, False to uncheck it.
        """
        self._require_confirmation(target, "changing")
        try:
            return await self.browser.toggle_checkbox(target, checked)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def submit_form(self, context: RunContext, target: str) -> dict[str, str]:
        """Submit the form containing the named control. Always requires user confirmation first.

        Before calling this, explain what the submission will do and get the user's
        explicit confirmation, then call confirm_browser_action with this same target.

        Args:
            target: The accessible name of a control inside the form, such as its submit button.
        """
        self._require_confirmation(target, "submitting", force=True)
        try:
            return await self.browser.submit_form(target)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def scroll(self, context: RunContext, direction: str) -> dict[str, str]:
        """Scroll the current browser page up or down.

        Args:
            direction: Either 'up' or 'down'.
        """
        try:
            return await self.browser.scroll(direction)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def press_key(self, context: RunContext, key: str) -> dict[str, str]:
        """Press a safe navigation key in the current browser page.

        Args:
            key: One of Enter, Escape, Tab, an arrow key, Backspace, Space, Home,
                End, PageUp, or PageDown.
        """
        try:
            return await self.browser.press_key(key)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    # ------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------
    @function_tool()
    async def list_tabs(self, context: RunContext) -> dict[str, object]:
        """List the browser's open tabs with their numbers, titles, and addresses."""
        try:
            return await self.browser.list_tabs()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def new_tab(self, context: RunContext, url: str = "") -> dict[str, object]:
        """Open a new browser tab and make it current, keeping existing tabs open.

        Args:
            url: Optional address to open in the new tab. Omit for a blank tab.
        """
        try:
            return await self.browser.new_tab(url or None)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def switch_tab(self, context: RunContext, index: int) -> dict[str, str]:
        """Switch to another open tab by its number from list_tabs.

        Args:
            index: The tab number to switch to.
        """
        try:
            return await self.browser.switch_tab(index)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def close_tab(
        self, context: RunContext, index: int | None = None
    ) -> dict[str, object]:
        """Close an open tab by its number, or the current tab if no number is given.

        Args:
            index: The tab number to close. Omit to close the current tab.
        """
        try:
            return await self.browser.close_tab(index)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc
