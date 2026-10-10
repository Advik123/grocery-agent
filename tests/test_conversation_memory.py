# tests/test_conversation_memory.py
# Unit tests for per-sender WhatsApp session state helpers.

import time

import pytest

from conversation_memory import (
    SESSION_TTL_SECONDS,
    clear_session,
    get_expired_store,
    get_last_store,
    has_pending_items,
    pop_pending_items,
    remember_store,
    set_pending_items,
)

PHONE = "+15550001234"


@pytest.fixture(autouse=True)
def clean_session():
    """Wipe the test sender's session before and after each test."""
    clear_session(PHONE)
    yield
    clear_session(PHONE)


# ---------------------------------------------------------------------------
# get_last_store / remember_store
# ---------------------------------------------------------------------------


def test_no_session_returns_none():
    assert get_last_store(PHONE) is None


def test_fresh_store_returned_within_ttl():
    remember_store(PHONE, "aldi")
    assert get_last_store(PHONE) == "aldi"


def test_expired_store_returns_none(monkeypatch):
    remember_store(PHONE, "aldi")
    # Simulate the TTL having elapsed by patching _now to return a future time.
    future = time.monotonic() + SESSION_TTL_SECONDS + 1
    import conversation_memory
    monkeypatch.setattr(conversation_memory, "_now", lambda: future)
    assert get_last_store(PHONE) is None


def test_remember_store_resets_ttl(monkeypatch):
    import conversation_memory

    remember_store(PHONE, "aldi")

    # Advance time to just past the TTL.
    past_expiry = time.monotonic() + SESSION_TTL_SECONDS + 1
    monkeypatch.setattr(conversation_memory, "_now", lambda: past_expiry)
    assert get_last_store(PHONE) is None

    # Calling remember_store again re-anchors the TTL to the (mocked) current time.
    remember_store(PHONE, "costco")
    assert get_last_store(PHONE) == "costco"


# ---------------------------------------------------------------------------
# get_expired_store
# ---------------------------------------------------------------------------


def test_get_expired_store_returns_store_after_ttl(monkeypatch):
    remember_store(PHONE, "walmart")
    future = time.monotonic() + SESSION_TTL_SECONDS + 1
    import conversation_memory
    monkeypatch.setattr(conversation_memory, "_now", lambda: future)
    # get_last_store returns None but get_expired_store still knows the store.
    assert get_last_store(PHONE) is None
    assert get_expired_store(PHONE) == "walmart"


def test_get_expired_store_none_when_no_history():
    assert get_expired_store(PHONE) is None


# ---------------------------------------------------------------------------
# pending items
# ---------------------------------------------------------------------------


def test_no_pending_by_default():
    assert not has_pending_items(PHONE)
    assert pop_pending_items(PHONE) is None


def test_set_and_pop_pending_items():
    items = {"eggs": 3, "milk": 1}
    set_pending_items(PHONE, items)
    assert has_pending_items(PHONE)
    result = pop_pending_items(PHONE)
    assert result == items
    # After pop, pending should be cleared.
    assert not has_pending_items(PHONE)
    assert pop_pending_items(PHONE) is None


def test_pop_clears_pending():
    set_pending_items(PHONE, {"bread": 2})
    pop_pending_items(PHONE)
    assert not has_pending_items(PHONE)
