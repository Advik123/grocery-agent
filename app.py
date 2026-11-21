# app.py
# Flask server exposing grocery API and WhatsApp webhook endpoints.
# Phase 2: a Swift iOS frontend will send HTTP requests to this server.
# Phase 3: ngrok tunneling will expose this server publicly; multi-store
# parallel requests and Siri App Intents will reuse the same endpoint.

import hashlib
import hmac
import os
import subprocess
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

from conversation_memory import (
    _SESSIONS,
    get_expired_store,
    get_last_store,
    has_pending_items,
    pop_pending_items,
    remember_store,
    set_pending_items,
)
from message_parser import parse_whatsapp_text
from parser import parse_items
from playwright_agent import add_to_list, warm_up
from store_config import STORE_LISTS, get_list_url
from whatsapp import parse_whatsapp_message, send_whatsapp_reply

load_dotenv()

app = Flask(__name__)

_START_TIME = time.monotonic()
_LAST_ADD_AT: list[str | None] = [None]

# Config vars reported (set / not set only) by GET /status.
_STATUS_CONFIG_VARS = (
    "GEMINI_API_KEY",
    "WA_PHONE_NUMBER_ID",
    "WA_ACCESS_TOKEN",
    "WA_VERIFY_TOKEN",
    "WA_APP_SECRET",
    "ADD_TOKEN",
    "STATUS_TOKEN",
    "SHORTCUT_TOKEN",
)

WHATSAPP_FORMAT_HINT = (
    "Include a store name (e.g. Aldi) and what to add, or use e.g. "
    '"aldi: eggs 3, milk 2".'
)

_AFFIRMATIVES = {"yes", "yeah", "yep", "y", "ok", "okay", "sure", "yup"}


# When set, replies are collected here instead of sent (used by /shortcut).
_REPLY_COLLECTOR: ContextVar[list[str] | None] = ContextVar(
    "reply_collector", default=None
)


def _record_add() -> None:
    """Remember the UTC time of the last successful list add."""
    _LAST_ADD_AT[0] = datetime.now(UTC).isoformat(timespec="seconds")


def _check_token(env_name: str) -> tuple[Response, int] | None:
    """Return an error response unless the request carries the *env_name* token.

    Accepts the token via ``X-Token`` or ``Authorization: Bearer <token>``.
    """
    expected = os.getenv(env_name)
    if not expected:
        return jsonify({"error": "endpoint is not configured"}), 503

    token = request.headers.get("X-Token", "")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not hmac.compare_digest(token.encode(), expected.encode()):
        return jsonify({"error": "unauthorized"}), 401
    return None


def _valid_wa_signature(secret: str, raw_body: bytes) -> bool:
    """Check Meta's X-Hub-Signature-256 header against *raw_body*."""
    header = request.headers.get("X-Hub-Signature-256", "")
    if not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:].encode(), digest.encode())


def _git_short_sha() -> str | None:
    """Return the short git commit SHA, or None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


def _reply_or_log(to: str, message: str) -> None:
    collector = _REPLY_COLLECTOR.get()
    if collector is not None:
        collector.append(message)
        return
    try:
        send_whatsapp_reply(to, message)
    except RuntimeError as exc:
        detail = f"failed to send WhatsApp reply: {exc}"
        if "401" in str(exc):
            detail += (
                " Check WA_ACCESS_TOKEN in .env — Meta test tokens expire "
                "after ~24h; create a permanent token or refresh in Developer "
                "Console → WhatsApp → API Setup."
            )
        print(detail)


@app.route("/add", methods=["POST"])
def add():
    """Accept a JSON payload and add items to the corresponding OurGroceries list.

    Expected body:
        {"store": "aldi", "items": {"eggs": 3, "milk": 2}}
    """
    denied = _check_token("ADD_TOKEN")
    if denied:
        return denied

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "request body must be JSON"}), 400

    # --- validate store ---
    store = body.get("store")
    if not store or not isinstance(store, str):
        return jsonify({"error": "'store' field is required and must be a string"}), 400
    store = store.strip().lower()

    try:
        url = get_list_url(store)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 400

    # --- validate items ---
    raw_items = body.get("items")
    if raw_items is None:
        return jsonify({"error": "'items' field is required"}), 400

    try:
        items = parse_items(raw_items)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # --- add items via Playwright ---
    try:
        added = add_to_list(url, items)
    except RuntimeError as exc:
        return jsonify({"error": f"playwright failed: {exc}"}), 500

    _record_add()
    return jsonify({"status": "success", "added": added, "store": store}), 200


@app.route("/whatsapp", methods=["GET"])
def verify_whatsapp():
    """Handle Meta's WhatsApp webhook verification handshake."""
    verify_token = os.getenv("WA_VERIFY_TOKEN")
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and verify_token and token == verify_token and challenge:
        return challenge, 200

    return "", 403


def _execute_add(phone: str, store: str, raw_items: dict[str, int]) -> None:
    """Validate *store* and *raw_items*, run Playwright, reply, and update session memory."""
    store = store.strip().lower()

    try:
        url = get_list_url(store)
    except KeyError as exc:
        _reply_or_log(phone, f"Sorry, {exc}. Check store_config.py.")
        return

    try:
        items = parse_items(raw_items)
    except ValueError as exc:
        _reply_or_log(phone, f"Sorry, {exc}. {WHATSAPP_FORMAT_HINT}")
        return

    try:
        added = add_to_list(url, items)
        _record_add()
        remember_store(phone, store)
        _reply_or_log(phone, f"Added: {', '.join(added)} to {store.title()} ✓")
    except RuntimeError as exc:
        _reply_or_log(phone, f"Sorry, I couldn't add your groceries right now. {exc}")


def _resolve_pending_confirmation(phone: str, text: str) -> None:
    """Handle a reply to the 'which store?' prompt that is waiting for the sender."""
    expired_store = get_expired_store(phone)

    # Affirmative reply → reuse the expired store
    if text.strip().lower() in _AFFIRMATIVES:
        items = pop_pending_items(phone)
        if items and expired_store:
            _execute_add(phone, expired_store, items)
        else:
            pop_pending_items(phone)
            _reply_or_log(phone, f"No previous store on record. {WHATSAPP_FORMAT_HINT}")
        return

    # Try to parse the reply as a grocery command
    try:
        store, raw_items = parse_whatsapp_text(text)
    except ValueError:
        store = None
        raw_items = {}

    if store and raw_items:
        # Full new command — discard the pending items and process fresh
        pop_pending_items(phone)
        _execute_add(phone, store, raw_items)
        return

    if store:
        # Store name only (Gemini recognised it but found no items)
        items = pop_pending_items(phone)
        if items:
            _execute_add(phone, store, items)
        else:
            _reply_or_log(phone, f"No pending items. {WHATSAPP_FORMAT_HINT}")
        return

    # Gemini found no store — check if the raw text is a known store name directly
    normalized = text.strip().lower()
    try:
        get_list_url(normalized)
        items = pop_pending_items(phone)
        if items:
            _execute_add(phone, normalized, items)
        else:
            _reply_or_log(phone, f"No pending items. {WHATSAPP_FORMAT_HINT}")
        return
    except KeyError:
        pass

    # Cannot resolve — re-prompt
    if expired_store:
        _reply_or_log(
            phone,
            f'Reply "yes" to add to {expired_store.title()}, '
            "or say a different store name.",
        )
    else:
        pop_pending_items(phone)
        _reply_or_log(phone, f"Couldn't understand that. {WHATSAPP_FORMAT_HINT}")


def _handle_message(phone: str, text: str) -> None:
    """Run *text* from sender *phone* through the shared grocery flow."""
    # --- pending confirmation: the user is answering "which store?" ---
    if has_pending_items(phone):
        _resolve_pending_confirmation(phone, text)
        return

    # --- parse the message ---
    try:
        store, raw_items = parse_whatsapp_text(text)
    except ValueError as exc:
        _reply_or_log(phone, f"Sorry, {exc}. {WHATSAPP_FORMAT_HINT}")
        return

    if store:
        # Explicit store included — add directly
        _execute_add(phone, store, raw_items)
        return

    # Items only — consult session memory
    fresh_store = get_last_store(phone)
    if fresh_store:
        _reply_or_log(phone, f"Using {fresh_store.title()} (last used store)…")
        _execute_add(phone, fresh_store, raw_items)
        return

    expired_store = get_expired_store(phone)
    if expired_store:
        set_pending_items(phone, raw_items)
        _reply_or_log(
            phone,
            f"Your {expired_store.title()} session expired. "
            f'Reply "yes" or "{expired_store.title()}" to add there, '
            "or say a different store name.",
        )
        return

    # No store context at all
    _reply_or_log(phone, f"I need a store name. {WHATSAPP_FORMAT_HINT}")


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Handle inbound WhatsApp messages and add parsed items to a store list."""
    secret = os.getenv("WA_APP_SECRET")
    if not secret:
        print("ERROR: WA_APP_SECRET is not set; rejecting WhatsApp webhook")
        return jsonify({"error": "webhook is not configured"}), 503

    raw_body = request.get_data()
    if not _valid_wa_signature(secret, raw_body):
        return jsonify({"error": "invalid signature"}), 403

    body = request.get_json(silent=True)
    if body is None:
        return "", 200

    parsed_message = parse_whatsapp_message(body)
    if parsed_message is None:
        return "", 200

    phone, text = parsed_message
    _handle_message(phone, text)
    return "", 200


def _shortcut_text() -> str:
    """Extract message text from a JSON, form, or plain-text request body."""
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        value = body.get("text")
        return value.strip() if isinstance(value, str) else ""
    if "text" in request.form:
        return request.form["text"].strip()
    return request.get_data(as_text=True).strip()


@app.route("/shortcut", methods=["POST"])
def shortcut():
    """Handle Apple Shortcuts / Siri text and return the agent reply as JSON."""
    expected = os.getenv("SHORTCUT_TOKEN")
    if not expected:
        return jsonify({"error": "shortcut endpoint is not configured"}), 503

    token = request.headers.get("X-Token", "")
    if not hmac.compare_digest(token.encode(), expected.encode()):
        return jsonify({"error": "unauthorized"}), 401

    text = _shortcut_text()
    if not text:
        return jsonify({"error": "'text' is required"}), 400

    sender = os.getenv("SHORTCUT_SENDER_ID") or "shortcut"
    replies: list[str] = []
    reset = _REPLY_COLLECTOR.set(replies)
    try:
        _handle_message(sender, text)
    except RuntimeError as exc:
        return jsonify({"error": f"could not process message: {exc}"}), 500
    finally:
        _REPLY_COLLECTOR.reset(reset)

    return jsonify({"reply": " ".join(replies)}), 200


@app.route("/status", methods=["GET"])
def status():
    """Return non-sensitive, read-only process info (no external calls)."""
    denied = _check_token("STATUS_TOKEN")
    if denied:
        return denied

    return jsonify(
        {
            "ok": True,
            "uptime_seconds": int(time.monotonic() - _START_TIME),
            "commit": _git_short_sha(),
            "port": int(os.getenv("PORT", "5000")),
            "config": {name: bool(os.getenv(name)) for name in _STATUS_CONFIG_VARS},
            "active_sessions": len(_SESSIONS),
            "last_add_at": _LAST_ADD_AT[0],
        }
    ), 200


def _warm_browser() -> None:
    """Start Chromium and open the first store's list so the first add is fast."""
    try:
        warm_up(next(iter(STORE_LISTS.values())))
    except RuntimeError as exc:
        print(f"WARNING: {exc}")


def _start_browser_warmup() -> None:
    """Warm the browser in the background when BROWSER_WARMUP is enabled."""
    if os.getenv("BROWSER_WARMUP", "").strip().lower() not in {"1", "true", "yes"}:
        return
    # The debug reloader's parent process never serves requests.
    reloader_parent = (
        __name__ == "__main__"
        and os.getenv("FLASK_ENV", "production") == "development"
        and not os.getenv("WERKZEUG_RUN_MAIN")
    )
    if not reloader_parent:
        threading.Thread(
            target=_warm_browser, name="browser-warmup", daemon=True
        ).start()


_start_browser_warmup()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
