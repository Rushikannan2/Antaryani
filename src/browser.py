"""Playwright-backed browser engine that gives the agent full page control.

This module only depends on Playwright: it knows nothing about LiveKit or the
agent. `BrowserTools` in `tools.py` exposes these methods to the LLM.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

NAV_TIMEOUT_MS = 15_000
ACTION_TIMEOUT_MS = 6_000
CONTENT_TIMEOUT_MS = 8_000
MAX_TEXT_CHARS = 6_000
PREVIEW_CHARS = 600
MAX_ELEMENTS = 60

# Every element the model may target. Query order defines the stable `ref`
# index that inspect/get_page_state report and click/type resolve against.
INTERACTIVE_SELECTOR = ", ".join(
    [
        "a[href]",
        "button",
        "input:not([type=hidden])",
        "select",
        "textarea",
        "summary",
        "[role]:not([role='presentation']):not([role='none'])",
        "[onclick]",
        "[tabindex]:not([tabindex='-1'])",
    ]
)

# Roles Playwright's get_by_role understands; used to prefer ARIA-native
# resolution and fall back to the indexed locator otherwise.
_ARIA_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
    "treeitem",
}

_ALLOWED_KEYS = {
    "Enter",
    "Escape",
    "Tab",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Backspace",
    "Space",
    "Home",
    "End",
    "PageUp",
    "PageDown",
}

_SCROLL_PIXELS = 600

_COLLECT_ELEMENTS_JS = """
(args) => {
  const { selector, limit } = args;
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.visibility !== "hidden" &&
      style.display !== "none"
    );
  };
  const textOf = (el) =>
    (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ");
  const nameOf = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return aria.trim();
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const parts = labelledBy
        .split(/\\s+/)
        .map((id) => {
          const node = document.getElementById(id);
          return node ? textOf(node) : "";
        })
        .filter(Boolean);
      if (parts.length) return parts.join(" ").trim();
    }
    const placeholder = el.getAttribute("placeholder");
    if (placeholder && placeholder.trim()) return placeholder.trim();
    if (el.id) {
      try {
        const label = document.querySelector(
          'label[for="' + CSS.escape(el.id) + '"]'
        );
        if (label) {
          const text = textOf(label);
          if (text) return text;
        }
      } catch (err) {
        // ignore invalid selectors
      }
    }
    const wrappingLabel = el.closest("label");
    if (wrappingLabel) {
      const text = textOf(wrappingLabel);
      if (text) return text;
    }
    const alt = el.getAttribute("alt");
    if (alt && alt.trim()) return alt.trim();
    const tag = el.tagName.toLowerCase();
    if (
      tag === "button" ||
      (tag === "input" &&
        ["button", "submit", "reset"].includes((el.type || "").toLowerCase()))
    ) {
      const value = el.value;
      if (value && value.trim()) return value.trim();
    }
    const text = textOf(el);
    if (text) return text;
    const title = el.getAttribute("title");
    if (title && title.trim()) return title.trim();
    const name = el.getAttribute("name");
    if (name && (tag === "input" || tag === "textarea")) return name;
    return "";
  };
  const roleOf = (el) => {
    const explicit = (el.getAttribute("role") || "").trim();
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "summary") return "button";
    if (tag === "input") {
      const type = (el.type || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "range") return "slider";
      if (type === "search") return "searchbox";
      if (type === "number") return "spinbutton";
      return "textbox";
    }
    return tag;
  };

  const nodes = Array.from(document.querySelectorAll(selector));
  const out = [];
  for (let i = 0; i < nodes.length; i += 1) {
    const el = nodes[i];
    if (!visible(el)) continue;
    if (el.getAttribute("aria-hidden") === "true") continue;
    out.push({
      ref: i,
      role: roleOf(el),
      name: nameOf(el),
      disabled: !!el.disabled,
    });
    if (out.length >= limit) break;
  }
  return out;
}
"""


class BrowserError(Exception):
    """Raised when a browser operation fails; converted to ToolError by tools."""


def normalize_element_name(value: str) -> str:
    """Normalize an accessible name for case/space-insensitive comparison."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_url(raw: str) -> str:
    """Validate and canonicalize a URL the agent wants to open.

    Adds https:// when no scheme is present and rejects anything that is not
    plain http(s), so the agent cannot reach file://, javascript:, or similar.
    """
    value = raw.strip()
    if not value:
        raise BrowserError("The URL cannot be empty.")
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme.lower() not in ("http", "https"):
            raise BrowserError(
                f"Only web pages can be opened, not {parsed.scheme} addresses."
            )
    else:
        value = f"https://{value}"
        parsed = urlparse(value)
    if not parsed.netloc:
        raise BrowserError(f"{raw!r} is not a valid web address.")
    return value


def pick_element(target: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick exactly one element from an inspection result for `target`.

    Matching order: exact accessible name, then name containment in either
    direction, then a unique role match. Ambiguous or missing matches raise
    `BrowserError` so the model is told to inspect and be more specific.
    """
    wanted = normalize_element_name(target)
    if not wanted:
        raise BrowserError("The target cannot be empty.")

    named = [
        el for el in elements if normalize_element_name(el.get("name", "")) == wanted
    ]
    if not named:
        contained = []
        for el in elements:
            name = normalize_element_name(el.get("name", ""))
            if not name:
                continue
            if wanted in name or (len(name) >= 3 and name in wanted):
                contained.append(el)
        named = contained
    if not named:
        by_role = [el for el in elements if el.get("role") == target.strip()]
        if len(by_role) == 1:
            return by_role[0]
        if len(by_role) > 1:
            named = by_role
        else:
            raise BrowserError(
                f"No interactive element matches {target!r}. "
                "Call inspect_page or get_page_state and use an exact name from the results."
            )
    if len(named) > 1:
        options = sorted({f"{el.get('role')} {el.get('name')!r}" for el in named})[:6]
        raise BrowserError(
            f"{target!r} matches several elements: {'; '.join(options)}. "
            "Be more specific so exactly one element matches."
        )
    return named[0]


class BrowserManager:
    """Owns a single Playwright browser page and serializes all access to it."""

    def __init__(
        self,
        headless: bool = True,
        screenshots_dir: str | Path = ".screenshots",
    ) -> None:
        env_headless = os.getenv("BROWSER_HEADLESS")
        if env_headless is not None:
            headless = env_headless.strip().lower() in {"1", "true", "yes"}
        self._headless = headless
        self._screenshots_dir = Path(screenshots_dir)
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Shut Playwright down. Safe to call more than once."""
        async with self._lock:
            await self._shutdown()

    async def _shutdown(self) -> None:
        for closeable in (
            self._context,
            self._browser,
        ):
            if closeable is not None:
                with contextlib.suppress(PlaywrightError):
                    await closeable.close()
        if self._pw is not None:
            with contextlib.suppress(PlaywrightError):
                await self._pw.stop()
        self._context = None
        self._browser = None
        self._page = None
        self._pw = None

    async def _ensure_page(self) -> Page:
        if self._page is not None and not self._page.is_closed():
            return self._page

        try:
            if self._pw is None:
                self._pw = await async_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                no_sandbox = os.getenv("BROWSER_NO_SANDBOX", "").lower() in {
                    "1",
                    "true",
                    "yes",
                }
                launch_kwargs: dict[str, Any] = {
                    "headless": self._headless,
                }
                if no_sandbox:
                    launch_kwargs["chromium_sandbox"] = False
                    launch_kwargs["args"] = ["--disable-dev-shm-usage"]
                self._browser = await self._pw.chromium.launch(**launch_kwargs)
            if self._context is None:
                self._context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    locale="en-GB",
                )
                self._context.set_default_timeout(ACTION_TIMEOUT_MS)
                self._context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            if not self._context.pages:
                self._page = await self._context.new_page()
            else:
                self._page = self._context.pages[-1]
        except PlaywrightError as exc:
            await self._shutdown()
            raise BrowserError(
                "Could not start the browser. "
                "Run `uv run playwright install chromium` and try again. "
                f"Details: {exc}"
            ) from exc
        return self._page

    # ------------------------------------------------------------------
    # navigation and reading
    # ------------------------------------------------------------------
    async def open_url(self, url: str) -> dict[str, str]:
        target = normalize_url(url)
        async with self._lock:
            page = await self._ensure_page()
            try:
                await page.goto(target, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise BrowserError(f"{target} took too long to respond.") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not open {target}: {exc}") from exc
            except Exception as exc:  # e.g. invalid URL from Chromium
                raise BrowserError(f"Could not open {target}: {exc}") from exc
            text = await self._page_text(page)
            return {
                "url": page.url,
                "title": await self._title(page),
                "preview": str(text["text"])[:PREVIEW_CHARS],
            }

    async def go_back(self) -> dict[str, str]:
        return await self._history_move("back")

    async def go_forward(self) -> dict[str, str]:
        return await self._history_move("forward")

    async def _history_move(self, direction: str) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            try:
                previous = page.url
                if direction == "back":
                    await page.go_back(wait_until="domcontentloaded")
                else:
                    await page.go_forward(wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise BrowserError("The previous page took too long to load.") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not go {direction}: {exc}") from exc
            if page.url == previous:
                raise BrowserError(
                    f"There is no page to the {direction} in this tab's history."
                )
            return {"url": page.url, "title": await self._title(page)}

    async def refresh(self) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            try:
                await page.reload(wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise BrowserError("The page took too long to reload.") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not reload the page: {exc}") from exc
            return {"url": page.url, "title": await self._title(page)}

    async def read_page(self) -> dict[str, str | bool]:
        async with self._lock:
            page = await self._ensure_page()
            return await self._page_text(page)

    async def inspect_page(self) -> dict[str, object]:
        async with self._lock:
            page = await self._ensure_page()
            return await self._page_elements(page)

    async def get_page_state(self) -> dict[str, object]:
        """Address, title, readable text, and interactive elements in one call."""
        async with self._lock:
            page = await self._ensure_page()
            return await self._page_state(page)

    async def wait_for_content(self, text: str | None = None) -> dict[str, object]:
        """Wait for the page to settle, or for `text` to appear on it."""
        async with self._lock:
            page = await self._ensure_page()
            if text is not None:
                needle = text.strip()
                if not needle:
                    raise BrowserError("The text to wait for cannot be empty.")
                try:
                    await page.get_by_text(needle, exact=False).first.wait_for(
                        state="visible", timeout=CONTENT_TIMEOUT_MS
                    )
                except PlaywrightTimeoutError as exc:
                    raise BrowserError(
                        f"{needle!r} did not appear on the page within "
                        f"{CONTENT_TIMEOUT_MS // 1000} seconds."
                    ) from exc
                except PlaywrightError as exc:
                    raise BrowserError(f"Could not wait for {needle!r}: {exc}") from exc
            else:
                # Endlessly-loading assets should not block the agent.
                with contextlib.suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state("load", timeout=CONTENT_TIMEOUT_MS)
                await asyncio.sleep(0.3)
            return await self._page_state(page)

    async def _page_state(self, page: Page) -> dict[str, object]:
        """Combine text and element inspection for `page`; caller holds the lock."""
        text_state = await self._page_text(page)
        element_state = await self._page_elements(page)
        return {**text_state, **element_state}

    # ------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------
    async def list_tabs(self) -> dict[str, object]:
        async with self._lock:
            context = await self._ensure_context()
            current = self._current_index()
            tabs = []
            for index, page in enumerate(context.pages):
                tabs.append(
                    {
                        "index": index,
                        "current": index == current,
                        "title": await self._title(page),
                        "url": page.url,
                    }
                )
            return {"current": current, "tabs": tabs}

    async def new_tab(self, url: str | None = None) -> dict[str, object]:
        target = normalize_url(url) if url and url.strip() else None
        async with self._lock:
            context = await self._ensure_context()
            page = await context.new_page()
            self._page = page
            if target:
                try:
                    await page.goto(target, wait_until="domcontentloaded")
                except PlaywrightTimeoutError as exc:
                    raise BrowserError(f"{target} took too long to respond.") from exc
                except PlaywrightError as exc:
                    raise BrowserError(f"Could not open {target}: {exc}") from exc
            return {
                "index": len(context.pages) - 1,
                "url": page.url,
                "title": await self._title(page),
            }

    async def switch_tab(self, index: int) -> dict[str, str]:
        async with self._lock:
            context = await self._ensure_context()
            page = self._page_at(context, index)
            self._page = page
            await page.bring_to_front()
            return {"url": page.url, "title": await self._title(page)}

    async def close_tab(self, index: int | None = None) -> dict[str, object]:
        async with self._lock:
            context = await self._ensure_context()
            if len(context.pages) <= 1:
                raise BrowserError("This is the only open tab, so it cannot be closed.")
            closing_index = self._current_index() if index is None else index
            page = self._page_at(context, closing_index)
            if self._page is page:
                self._page = None
            await page.close()
            remaining = context.pages
            new_index = min(closing_index, len(remaining) - 1)
            self._page = remaining[new_index]
            await self._page.bring_to_front()
            return {"closed": closing_index, "current": new_index}

    def _page_at(self, context: BrowserContext, index: int) -> Page:
        if not isinstance(index, int) or index < 0 or index >= len(context.pages):
            raise BrowserError(
                f"There is no tab {index!r}; there are {len(context.pages)} open tabs."
            )
        return context.pages[index]

    def _current_index(self) -> int:
        assert self._context is not None and self._page is not None
        for index, page in enumerate(self._context.pages):
            if page is self._page:
                return index
        return 0

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------
    async def click(self, target: str) -> dict[str, str]:
        return await self._pointer_action("click", target)

    async def double_click(self, target: str) -> dict[str, str]:
        return await self._pointer_action("dblclick", target)

    async def hover(self, target: str) -> dict[str, str]:
        return await self._pointer_action("hover", target)

    async def _pointer_action(self, action: str, target: str) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            locator = await self._resolve(page, target)
            try:
                if action == "click":
                    await locator.click(timeout=ACTION_TIMEOUT_MS)
                elif action == "dblclick":
                    await locator.dblclick(timeout=ACTION_TIMEOUT_MS)
                else:
                    await locator.hover(timeout=ACTION_TIMEOUT_MS)
            except PlaywrightTimeoutError as exc:
                raise BrowserError(
                    f"Could not {action} {target!r}: the element is not visible or enabled."
                ) from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not {action} {target!r}: {exc}") from exc
            await self._settle(page)
            return {"url": page.url, "title": await self._title(page)}

    async def type_text(self, target: str, text: str) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            locator = await self._resolve(page, target)
            try:
                await locator.fill(text, timeout=ACTION_TIMEOUT_MS)
            except PlaywrightTimeoutError as exc:
                raise BrowserError(
                    f"Could not type into {target!r}: the field is not visible or editable."
                ) from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not type into {target!r}: {exc}") from exc
            return {"url": page.url, "title": await self._title(page)}

    async def clear_field(self, target: str) -> dict[str, str]:
        return await self.type_text(target, "")

    async def select_option(self, target: str, value: str) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            locator = await self._resolve(page, target)
            try:
                options: list[dict[str, str]] = await locator.evaluate(
                    "el => Array.from(el.options || []).map("
                    "o => ({value: o.value, label: (o.text || '').trim()}))"
                )
            except PlaywrightError as exc:
                raise BrowserError(f"{target!r} is not a dropdown list.") from exc
            if not options:
                raise BrowserError(f"{target!r} has no options to choose from.")

            wanted = normalize_element_name(value)
            matched = [
                o for o in options if normalize_element_name(o["label"]) == wanted
            ]
            if not matched:
                matched = [
                    o for o in options if normalize_element_name(o["value"]) == wanted
                ]
            if not matched:
                matched = [
                    o
                    for o in options
                    if wanted in normalize_element_name(o["label"])
                    or normalize_element_name(o["label"]) in wanted
                ]
            if not matched:
                available = ", ".join(o["label"] or o["value"] for o in options[:15])
                raise BrowserError(
                    f"{value!r} is not an option of {target!r}. Available options: {available}."
                )
            if len(matched) > 1:
                available = ", ".join(o["label"] or o["value"] for o in matched[:15])
                raise BrowserError(
                    f"{value!r} matches several options of {target!r}: {available}. "
                    "Be more specific."
                )
            try:
                await locator.select_option(
                    value=matched[0]["value"], timeout=ACTION_TIMEOUT_MS
                )
            except PlaywrightTimeoutError as exc:
                raise BrowserError(
                    f"Could not select {value!r} in {target!r}."
                ) from exc
            except PlaywrightError as exc:
                raise BrowserError(
                    f"Could not select {value!r} in {target!r}: {exc}"
                ) from exc
            return {
                "url": page.url,
                "title": await self._title(page),
                "selected": matched[0]["label"] or matched[0]["value"],
            }

    async def toggle_checkbox(self, target: str, checked: bool) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            locator = await self._resolve(page, target)
            try:
                currently = await locator.is_checked(timeout=ACTION_TIMEOUT_MS)
            except PlaywrightTimeoutError as exc:
                raise BrowserError(f"{target!r} is not a checkbox or switch.") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not read {target!r}: {exc}") from exc
            if currently != checked:
                try:
                    await locator.click(timeout=ACTION_TIMEOUT_MS)
                except PlaywrightError as exc:
                    raise BrowserError(f"Could not change {target!r}: {exc}") from exc
            return {
                "url": page.url,
                "title": await self._title(page),
                "checked": checked,
            }

    async def submit_form(self, target: str) -> dict[str, str]:
        async with self._lock:
            page = await self._ensure_page()
            locator = await self._resolve(page, target)
            try:
                in_form: bool = await locator.evaluate(
                    "el => !!(el.form || el.closest('form'))"
                )
            except PlaywrightError as exc:
                raise BrowserError(f"Could not inspect {target!r}: {exc}") from exc
            if not in_form:
                raise BrowserError(f"{target!r} is not inside a form.")
            try:
                await locator.evaluate(
                    "el => { const form = el.form || el.closest('form'); form.requestSubmit(); }"
                )
            except PlaywrightError as exc:
                raise BrowserError(
                    f"Could not submit the form with {target!r}: {exc}"
                ) from exc
            await self._settle(page)
            return {"url": page.url, "title": await self._title(page)}

    async def scroll(self, direction: str) -> dict[str, str]:
        normalized = direction.strip().casefold()
        if normalized not in ("up", "down"):
            raise BrowserError("Direction must be 'up' or 'down'.")
        async with self._lock:
            page = await self._ensure_page()
            distance = _SCROLL_PIXELS if normalized == "down" else -_SCROLL_PIXELS
            await page.mouse.move(640, 400)
            await page.mouse.wheel(0, distance)
            return {
                "url": page.url,
                "title": await self._title(page),
                "scrolled": normalized,
            }

    async def press_key(self, key: str) -> dict[str, str]:
        canonical = next(
            (
                allowed
                for allowed in _ALLOWED_KEYS
                if allowed.casefold() == key.strip().casefold()
            ),
            None,
        )
        if canonical is None:
            allowed = ", ".join(sorted(_ALLOWED_KEYS))
            raise BrowserError(f"Only these keys are allowed: {allowed}.")
        async with self._lock:
            page = await self._ensure_page()
            try:
                await page.keyboard.press(canonical)
            except PlaywrightError as exc:
                raise BrowserError(f"Could not press {canonical}: {exc}") from exc
            return {"url": page.url, "title": await self._title(page), "key": canonical}

    async def take_screenshot(self) -> dict[str, str | int]:
        """Save a viewport screenshot and return its metadata.

        The tool layer reads `saved_path` to add the image to the model's
        chat context, so this module stays free of LiveKit imports.
        """
        async with self._lock:
            page = await self._ensure_page()
            self._screenshots_dir.mkdir(parents=True, exist_ok=True)
            stem = (
                re.sub(r"[^a-zA-Z0-9_-]+", "-", await self._title(page))[:40] or "page"
            )
            path = self._screenshots_dir / f"{stem}-{self._timestamp()}.jpg"
            try:
                await page.screenshot(path=str(path), type="jpeg", quality=60)
            except PlaywrightError as exc:
                raise BrowserError(f"Could not capture the page: {exc}") from exc
            return {
                "saved_path": str(path.resolve()),
                "url": page.url,
                "title": await self._title(page),
                "size_bytes": path.stat().st_size,
            }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _ensure_context(self) -> BrowserContext:
        await self._ensure_page()
        assert self._context is not None
        return self._context

    async def _resolve(self, page: Page, target: str) -> Locator:
        elements = await self._collect(page)
        picked = pick_element(target, elements)
        role = picked.get("role", "")
        name = picked.get("name", "")
        if name and role in _ARIA_ROLES:
            try:
                locator = page.get_by_role(role, name=name, exact=True)
                if await locator.count() > 0:
                    return locator.first
            except PlaywrightError:
                pass
        return page.locator(INTERACTIVE_SELECTOR).nth(picked["ref"])

    async def _collect(self, page: Page) -> list[dict[str, Any]]:
        try:
            return await page.evaluate(
                _COLLECT_ELEMENTS_JS,
                {"selector": INTERACTIVE_SELECTOR, "limit": MAX_ELEMENTS},
            )
        except PlaywrightError as exc:
            raise BrowserError(f"Could not inspect the page: {exc}") from exc

    async def _page_text(self, page: Page) -> dict[str, str | bool]:
        try:
            text = await page.evaluate(
                "() => (document.body && document.body.innerText) || ''"
            )
        except PlaywrightError as exc:
            raise BrowserError(f"Could not read the page: {exc}") from exc
        text = text or ""
        truncated = len(text) > MAX_TEXT_CHARS
        return {
            "url": page.url,
            "title": await self._title(page),
            "text": (text[:MAX_TEXT_CHARS] + "\n[truncated]") if truncated else text,
            "truncated": truncated,
        }

    async def _page_elements(self, page: Page) -> dict[str, object]:
        elements = await self._collect(page)
        truncated = len(elements) >= MAX_ELEMENTS
        return {
            "elements": [
                {
                    "ref": el["ref"],
                    "role": el["role"],
                    "name": el["name"],
                    **({"disabled": True} if el.get("disabled") else {}),
                }
                for el in elements
            ],
            "elements_truncated": truncated,
        }

    async def _title(self, page: Page) -> str:
        try:
            return (await page.title()).strip()
        except PlaywrightError:
            return ""

    async def _settle(self, page: Page) -> None:
        """Give the page a brief moment after an action that may navigate."""
        with contextlib.suppress(PlaywrightTimeoutError, PlaywrightError):
            await page.wait_for_load_state("domcontentloaded", timeout=2_000)

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
