# Tests for the long-lived browser in playwright_agent (Playwright fully mocked).

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import playwright_agent
from playwright_agent import ADD_BUTTON, ITEM_INPUT, add_to_list, shutdown, warm_up

ALDI = "https://www.ourgroceries.com/your-lists/list/aldi"
COSTCO = "https://www.ourgroceries.com/your-lists/list/costco"
SIGN_IN = "https://www.ourgroceries.com/sign-in"
CLOSED = "Target page, context or browser has been closed"


def _make_page() -> MagicMock:
    page = MagicMock()
    page.url = "about:blank"
    page.is_closed.return_value = False

    async def goto(url, **kwargs):
        page.url = url

    page.goto = AsyncMock(side_effect=goto)
    page.wait_for_function = AsyncMock()
    locators = {}
    for selector in (ADD_BUTTON, ITEM_INPUT):
        loc = MagicMock()
        for method in ("wait_for", "click", "fill", "press"):
            setattr(loc, method, AsyncMock())
        loc.is_visible = AsyncMock(return_value=False)
        locators[selector] = loc
    page.locator.side_effect = locators.__getitem__
    page.add_button = locators[ADD_BUTTON]
    page.item_input = locators[ITEM_INPUT]
    return page


def _make_context() -> MagicMock:
    context = MagicMock()
    context.page = _make_page()
    context.pages = [context.page]
    context.close = AsyncMock()
    context.new_page = AsyncMock(side_effect=lambda: _make_page())
    return context


def _close_handler(context: MagicMock):
    """Return the "close" listener the agent registered on *context*."""
    for call in context.on.call_args_list:
        if call.args[0] == "close":
            return call.args[1]
    raise AssertionError("no close listener registered")


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """Mock async_playwright; each launch returns the next prepared context."""
    monkeypatch.setattr(playwright_agent, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(playwright_agent, "_SETTLE_SECONDS", 0.0)
    contexts = [_make_context() for _ in range(3)]
    pw = MagicMock()
    pw.stop = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(side_effect=contexts)
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    with patch("playwright_agent.async_playwright", return_value=starter):
        yield SimpleNamespace(
            pw=pw,
            launch=pw.chromium.launch_persistent_context,
            contexts=contexts,
            page=contexts[0].page,
        )
    shutdown()


def _filled(page: MagicMock) -> list[str]:
    return [call.args[0] for call in page.item_input.fill.call_args_list]


def test_adds_items_in_order(browser):
    assert add_to_list(ALDI, {"eggs": 3, "milk": 1}) == ["eggs x3", "milk x1"]
    assert _filled(browser.page) == ["eggs x3", "milk x1"]
    assert browser.page.item_input.press.call_count == 2
    browser.page.goto.assert_awaited_once_with(ALDI, wait_until="domcontentloaded")
    browser.page.add_button.wait_for.assert_awaited()


def test_second_call_reuses_browser_and_page(browser):
    add_to_list(ALDI, {"eggs": 1})
    add_to_list(ALDI, {"milk": 2})
    browser.launch.assert_awaited_once()
    browser.page.goto.assert_awaited_once()
    assert _filled(browser.page) == ["eggs x1", "milk x2"]


def test_navigates_only_when_list_changes(browser):
    add_to_list(ALDI, {"eggs": 1})
    add_to_list(COSTCO, {"milk": 1})
    add_to_list(COSTCO, {"bread": 1})
    browser.launch.assert_awaited_once()
    assert [c.args[0] for c in browser.page.goto.await_args_list] == [ALDI, COSTCO]


def test_reuses_open_add_dialog(browser):
    browser.page.item_input.is_visible.return_value = True
    add_to_list(ALDI, {"eggs": 1})
    browser.page.add_button.click.assert_not_awaited()


def test_closed_context_is_relaunched(browser):
    add_to_list(ALDI, {"eggs": 1})
    first = browser.contexts[0]
    _close_handler(first)(first)  # browser exited between requests

    assert add_to_list(ALDI, {"milk": 1}) == ["milk x1"]
    assert browser.launch.await_count == 2
    assert _filled(browser.contexts[1].page) == ["milk x1"]


def test_crash_mid_add_relaunches_once_without_duplicates(browser):
    first_page = browser.contexts[0].page
    first_page.item_input.fill.side_effect = [None, PlaywrightError(CLOSED)]

    assert add_to_list(ALDI, {"eggs": 1, "milk": 2}) == ["eggs x1", "milk x2"]
    assert browser.launch.await_count == 2
    browser.contexts[0].close.assert_awaited()
    assert _filled(browser.contexts[1].page) == ["milk x2"]


def test_repeated_crash_raises_runtime_error(browser):
    for context in browser.contexts:
        context.page.goto.side_effect = PlaywrightError(CLOSED)
    with pytest.raises(RuntimeError, match="automation failed"):
        add_to_list(ALDI, {"eggs": 1})
    assert browser.launch.await_count == 2


def test_other_playwright_error_raises_without_relaunch(browser):
    browser.page.add_button.click.side_effect = PlaywrightTimeoutError("timed out")
    with pytest.raises(RuntimeError, match="automation failed"):
        add_to_list(ALDI, {"eggs": 1})
    browser.launch.assert_awaited_once()

    browser.page.add_button.click.side_effect = None
    add_to_list(ALDI, {"eggs": 1})
    assert browser.page.goto.await_count == 2  # page reloaded after the failure


def test_sign_in_redirect_raises_runtime_error(browser):
    async def redirect(url, **kwargs):
        browser.page.url = SIGN_IN

    browser.page.goto.side_effect = redirect
    with pytest.raises(RuntimeError, match="Login session expired"):
        add_to_list(ALDI, {"eggs": 1})
    browser.page.item_input.fill.assert_not_awaited()


def test_client_side_sign_in_redirect_raises_runtime_error(browser):
    async def late_redirect(**kwargs):
        browser.page.url = SIGN_IN
        raise PlaywrightTimeoutError("timed out")

    browser.page.add_button.wait_for.side_effect = late_redirect
    with pytest.raises(RuntimeError, match="Login session expired"):
        add_to_list(ALDI, {"eggs": 1})


def test_submit_wait_timeout_falls_back(browser):
    browser.page.wait_for_function.side_effect = PlaywrightTimeoutError("slow")
    assert add_to_list(ALDI, {"eggs": 1}) == ["eggs x1"]


def test_missing_profile_raises_runtime_error(browser, tmp_path, monkeypatch):
    monkeypatch.setattr(playwright_agent, "PROFILE_DIR", tmp_path / "missing")
    with pytest.raises(RuntimeError, match="No browser profile"):
        add_to_list(ALDI, {"eggs": 1})
    browser.launch.assert_not_awaited()


def test_warm_up_opens_list_for_first_add(browser):
    warm_up(ALDI)
    add_to_list(ALDI, {"eggs": 1})
    browser.launch.assert_awaited_once()
    browser.page.goto.assert_awaited_once()


def test_shutdown_closes_browser(browser):
    add_to_list(ALDI, {"eggs": 1})
    shutdown()
    browser.contexts[0].close.assert_awaited_once()
    browser.pw.stop.assert_awaited_once()
    assert playwright_agent._RUNNER.loop is None
