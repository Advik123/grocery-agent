# playwright_agent.py
# Adds grocery items to OurGroceries lists through one long-lived headless
# Chromium (persistent profile at .browser-profile/).
#
# Unlike the other modules this one holds state: a single background thread runs
# an asyncio event loop that owns the Playwright driver, the browser context and
# one reusable page.  Playwright objects are bound to the loop that created them,
# so Flask request threads never touch them directly; add_to_list() is a plain
# synchronous call that submits work to that loop and waits, one add at a time.
# The browser starts lazily (or via warm_up()), is relaunched once if it has
# crashed or closed, and is closed by an atexit hook.
# State is per process: with N gunicorn workers there are N browsers.
# Phase 3: multi-store parallel requests would need one page per list.

import asyncio
import atexit
import concurrent.futures
import os
import threading
import time
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

# Persistent browser profile directory — stores login cookies between runs.
PROFILE_DIR = Path(__file__).parent / ".browser-profile"

ADD_BUTTON = "button.addItemButton"
ITEM_INPUT = "#addItemName"

_LIST_LOAD_TIMEOUT_MS = 15_000
_INPUT_TIMEOUT_MS = 5_000
_SUBMIT_TIMEOUT_MS = 3_000
_CALL_TIMEOUT_SECONDS = 90.0
_SHUTDOWN_TIMEOUT_SECONDS = 10.0
# Reload a list page left open this long, so a stale session or UI is noticed.
_PAGE_MAX_AGE_SECONDS = 15 * 60.0
# OurGroceries syncs an add in the background after the input clears; give it
# this long before navigating away or closing the browser.
_SETTLE_SECONDS = 1.0

# Images are not needed to drive the UI; skipping them saves memory on the Pi.
_CHROMIUM_ARGS = [
    "--blink-settings=imagesEnabled=false",
    "--disable-extensions",
    "--mute-audio",
    "--no-first-run",
]

_SESSION_EXPIRED = "Login session expired.  Re-run login.py to log in again."

# True once Enter was handled: the add input was cleared or closed and the item
# name is on the page.
_SUBMITTED_JS = """([selector, text, name]) => {
    const input = document.querySelector(selector);
    const cleared = !input || input.offsetParent === null || input.value !== text;
    return cleared && document.body.innerText.toLowerCase().includes(name);
}"""

T = TypeVar("T")


@dataclass
class _Runner:
    """Browser state for this process; only the loop thread touches Playwright."""

    pid: int = 0
    loop: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    playwright: Playwright | None = None
    context: BrowserContext | None = None
    page: Page | None = None
    page_url: str | None = None
    loaded_at: float = 0.0
    last_add_at: float = 0.0


_RUNNER = _Runner()
# Serializes adds (one shared page) and startup/shutdown of the loop thread.
_LOCK = threading.Lock()


# --- event-loop thread (called with _LOCK held) ------------------------------


def _event_loop() -> asyncio.AbstractEventLoop:
    """Return the browser thread's event loop, starting the thread if needed."""
    global _RUNNER
    runner = _RUNNER
    if runner.pid != os.getpid():
        # Threads do not survive fork (e.g. gunicorn --preload); start fresh.
        runner = _RUNNER = _Runner(pid=os.getpid())
    if runner.loop is None or runner.thread is None or not runner.thread.is_alive():
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=loop.run_forever, name="playwright-loop", daemon=True
        )
        thread.start()
        runner.loop, runner.thread = loop, thread
        runner.playwright = runner.context = runner.page = None
        runner.page_url = None
    return runner.loop


def _run(coro: Coroutine[Any, Any, T], timeout: float) -> T:
    """Run *coro* on the browser thread and wait up to *timeout* seconds."""
    future = asyncio.run_coroutine_threadsafe(coro, _event_loop())
    try:
        return future.result(timeout)
    except TimeoutError as exc:
        future.cancel()
        concurrent.futures.wait([future], timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        _RUNNER.page_url = None
        raise RuntimeError(f"browser did not respond within {timeout:.0f} s") from exc


# --- browser lifecycle (runs on the loop thread) -----------------------------


def _forget_context(context: BrowserContext) -> None:
    """Drop *context* from the runner once it has closed or the browser died."""
    if _RUNNER.context is context:
        _RUNNER.context = _RUNNER.page = None
        _RUNNER.page_url = None


def _forget_page(page: Page) -> None:
    """Drop *page* from the runner after its renderer crashed."""
    if _RUNNER.page is page:
        _RUNNER.page = None
        _RUNNER.page_url = None


def _track_page(page: Page) -> Page:
    """Make *page* the shared page and watch it for renderer crashes."""
    page.on("crash", lambda _: _forget_page(page))
    _RUNNER.page, _RUNNER.page_url = page, None
    return page


async def _launch() -> BrowserContext:
    """Start Playwright and the persistent Chromium context."""
    await _close_browser()
    print("[playwright] launching Chromium", flush=True)
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=True, args=_CHROMIUM_ARGS
        )
    except PlaywrightError:
        await playwright.stop()
        raise
    context.on("close", lambda _: _forget_context(context))
    _RUNNER.playwright, _RUNNER.context = playwright, context
    _track_page(context.pages[0] if context.pages else await context.new_page())
    return context


async def _close_browser() -> None:
    """Close the context and Playwright driver, ignoring ones already dead."""
    context, playwright = _RUNNER.context, _RUNNER.playwright
    _RUNNER.playwright = _RUNNER.context = _RUNNER.page = None
    _RUNNER.page_url = None
    if context is not None:
        with suppress(PlaywrightError):
            await context.close()
    if playwright is not None:
        with suppress(PlaywrightError):
            await playwright.stop()


async def _settle() -> None:
    """Wait until the last add has had _SETTLE_SECONDS to sync."""
    if _RUNNER.last_add_at:
        remaining = _SETTLE_SECONDS - (time.monotonic() - _RUNNER.last_add_at)
        if remaining > 0:
            await asyncio.sleep(remaining)


async def _get_page() -> Page:
    """Return the shared page, launching the browser or a new tab if needed."""
    context = _RUNNER.context or await _launch()
    page = _RUNNER.page
    if page is None or page.is_closed():
        page = _track_page(await context.new_page())
    return page


# --- OurGroceries UI (runs on the loop thread) -------------------------------


async def _open_list(page: Page, url: str) -> None:
    """Show the list at *url*, skipping navigation when it is already open."""
    age = time.monotonic() - _RUNNER.loaded_at
    if _RUNNER.page_url == url and age < _PAGE_MAX_AGE_SECONDS:
        if "/sign-in" in page.url:
            _RUNNER.page_url = None
            raise RuntimeError(_SESSION_EXPIRED)
        return

    await _settle()
    _RUNNER.page_url = None
    await page.goto(url, wait_until="domcontentloaded")
    if "/sign-in" not in page.url:
        try:
            await page.locator(ADD_BUTTON).wait_for(
                state="visible", timeout=_LIST_LOAD_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            if "/sign-in" not in page.url:
                raise
    # The redirect can happen server-side (before goto returns) or in page JS.
    if "/sign-in" in page.url:
        raise RuntimeError(_SESSION_EXPIRED)
    _RUNNER.page_url, _RUNNER.loaded_at = url, time.monotonic()


async def _submit_item(page: Page, formatted: str, name: str) -> None:
    """Type *formatted* into the add-item box and wait for the list to take it."""
    item_input = page.locator(ITEM_INPUT)
    if not await item_input.is_visible():
        await page.locator(ADD_BUTTON).click()
        await item_input.wait_for(state="visible", timeout=_INPUT_TIMEOUT_MS)
    await item_input.fill(formatted)
    await item_input.press("Enter")
    try:
        await page.wait_for_function(
            _SUBMITTED_JS,
            arg=[ITEM_INPUT, formatted, name.lower()],
            timeout=_SUBMIT_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        # Same outcome as the old fixed sleep: assume it went through.
        print(f"[playwright] no confirmation for {formatted!r}; continuing", flush=True)


async def _add_pending(
    url: str, pending: list[tuple[str, str]], added: list[str]
) -> list[str]:
    """Add the *pending* (formatted, name) pairs not yet in *added*."""
    page = await _get_page()
    await _open_list(page, url)
    for formatted, name in pending[len(added) :]:
        await _submit_item(page, formatted, name)
        added.append(formatted)
        _RUNNER.last_add_at = time.monotonic()
    return added


def _browser_lost(exc: PlaywrightError) -> bool:
    """Return True if *exc* means the browser, context or page went away."""
    page = _RUNNER.page
    message = str(exc).lower()
    return (
        _RUNNER.context is None
        or page is None
        or page.is_closed()
        or "closed" in message
        or "crash" in message
    )


async def _add_items(url: str, items: dict[str, int]) -> list[str]:
    """Add *items* to the list at *url*, relaunching a dead browser once."""
    pending = [(f"{name} x{qty}", name) for name, qty in items.items()]
    added: list[str] = []
    try:
        return await _add_pending(url, pending, added)
    except PlaywrightError as exc:
        _RUNNER.page_url = None
        if not _browser_lost(exc):
            raise RuntimeError(f"OurGroceries automation failed: {exc}") from exc
        print(f"[playwright] browser lost ({exc}); relaunching", flush=True)
        await _close_browser()

    # Only items not confirmed before the crash are retried.
    try:
        return await _add_pending(url, pending, added)
    except PlaywrightError as exc:
        _RUNNER.page_url = None
        raise RuntimeError(f"OurGroceries automation failed: {exc}") from exc


async def _warm(url: str) -> None:
    """Launch the browser and open the list at *url*."""
    try:
        await _open_list(await _get_page(), url)
    except PlaywrightError as exc:
        raise RuntimeError(f"browser warm-up failed: {exc}") from exc


# --- public API ----------------------------------------------------------------


def _require_profile() -> None:
    """Raise RuntimeError unless login.py has created the browser profile."""
    if not PROFILE_DIR.exists():
        raise RuntimeError(
            "No browser profile found.  Run login.py first "
            "to create a login session."
        )


def add_to_list(url: str, items: dict[str, int]) -> list[str]:
    """Open the OurGroceries list at *url* and add each item.

    Items are formatted as "name x<qty>" (e.g. "eggs x3").

    Returns a list of the formatted item strings that were submitted.

    Requires that .browser-profile/ exists with a valid login session
    (created via login.py).  Safe to call from any thread; calls run one at a
    time on the shared browser.  Raises RuntimeError on any browser failure.
    """
    _require_profile()
    start = time.perf_counter()
    try:
        with _LOCK:
            return _run(_add_items(url, items), _CALL_TIMEOUT_SECONDS)
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(
            f"[timing] playwright_add {elapsed_ms:.0f} ms ({len(items)} items)",
            flush=True,
        )


def warm_up(url: str) -> None:
    """Start the browser and open the list at *url* so the first add is fast."""
    _require_profile()
    start = time.perf_counter()
    with _LOCK:
        _run(_warm(url), _CALL_TIMEOUT_SECONDS)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"[timing] browser_warm_up {elapsed_ms:.0f} ms", flush=True)


async def _settle_and_close() -> None:
    """Let the last add sync, then close the browser."""
    await _settle()
    await _close_browser()


def shutdown() -> None:
    """Close the browser and stop the loop thread (also registered with atexit)."""
    global _RUNNER
    acquired = _LOCK.acquire(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    try:
        runner = _RUNNER
        loop, thread = runner.loop, runner.thread
        if runner.pid != os.getpid() or loop is None or thread is None:
            return
        if thread.is_alive():
            future = asyncio.run_coroutine_threadsafe(_settle_and_close(), loop)
            with suppress(PlaywrightError, TimeoutError):
                future.result(_SHUTDOWN_TIMEOUT_SECONDS)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(_SHUTDOWN_TIMEOUT_SECONDS)
        if not thread.is_alive():
            loop.close()
        _RUNNER = _Runner()
    finally:
        if acquired:
            _LOCK.release()


atexit.register(shutdown)
