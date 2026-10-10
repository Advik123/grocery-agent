# conversation_memory.py
# In-memory per-sender WhatsApp session state.
# Tracks the last store used (with a TTL) and any items pending store confirmation.
# Phase 2: replace _SESSIONS with a Redis/SQLite backend if multiple workers are needed.

import time

SESSION_TTL_SECONDS = 120  # 2 minutes

# Schema per phone number:
# {
#   "last_store": str | None,
#   "last_used_at": float,          # time.monotonic() timestamp
#   "pending_items": dict[str, int] | None,  # items awaiting store confirmation
# }
_SESSIONS: dict[str, dict] = {}


def _now() -> float:
    return time.monotonic()


def _session(phone: str) -> dict:
    """Return (creating if absent) the session record for *phone*."""
    if phone not in _SESSIONS:
        _SESSIONS[phone] = {
            "last_store": None,
            "last_used_at": 0.0,
            "pending_items": None,
        }
    return _SESSIONS[phone]


def get_last_store(phone: str) -> str | None:
    """Return the last store for *phone* if still within the TTL, else None."""
    s = _session(phone)
    if s["last_store"] and (_now() - s["last_used_at"]) <= SESSION_TTL_SECONDS:
        return s["last_store"]
    return None


def get_expired_store(phone: str) -> str | None:
    """Return the last store for *phone* even if the TTL has elapsed, else None.

    Used to offer the user a confirmation prompt when their session has just expired.
    """
    return _session(phone)["last_store"]


def remember_store(phone: str, store: str) -> None:
    """Record *store* as the active session store for *phone* and reset the TTL."""
    s = _session(phone)
    s["last_store"] = store
    s["last_used_at"] = _now()


def set_pending_items(phone: str, items: dict[str, int]) -> None:
    """Park *items* on *phone*'s session until the user confirms which store to use."""
    _session(phone)["pending_items"] = items


def pop_pending_items(phone: str) -> dict[str, int] | None:
    """Remove and return pending items for *phone*, or None if none are waiting."""
    s = _session(phone)
    items = s["pending_items"]
    s["pending_items"] = None
    return items


def has_pending_items(phone: str) -> bool:
    """Return True if *phone* has items waiting for store confirmation."""
    return bool(_session(phone)["pending_items"])


def clear_session(phone: str) -> None:
    """Wipe all session state for *phone* (useful in tests or explicit resets)."""
    _SESSIONS.pop(phone, None)
