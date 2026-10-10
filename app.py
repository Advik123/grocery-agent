# app.py
# Flask server exposing grocery API and WhatsApp webhook endpoints.
# Phase 2: a Swift iOS frontend will send HTTP requests to this server.
# Phase 3: ngrok tunneling will expose this server publicly; multi-store
# parallel requests and Siri App Intents will reuse the same endpoint.

import asyncio
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from conversation_memory import (
    get_expired_store,
    get_last_store,
    has_pending_items,
    pop_pending_items,
    remember_store,
    set_pending_items,
)
from message_parser import parse_whatsapp_text
from parser import parse_items
from playwright_agent import add_to_list
from store_config import get_list_url
from whatsapp import parse_whatsapp_message, send_whatsapp_reply

load_dotenv()

app = Flask(__name__)

WHATSAPP_FORMAT_HINT = (
    "Include a store name (e.g. Aldi) and what to add, or use e.g. "
    '"aldi: eggs 3, milk 2".'
)

_AFFIRMATIVES = {"yes", "yeah", "yep", "y", "ok", "okay", "sure", "yup"}


def _reply_or_log(to: str, message: str) -> None:
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
        added = asyncio.run(add_to_list(url, items))
    except Exception as exc:
        return jsonify({"error": f"playwright failed: {exc}"}), 500

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
        added = asyncio.run(add_to_list(url, items))
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


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Handle inbound WhatsApp messages and add parsed items to a store list."""
    body = request.get_json(silent=True)
    if body is None:
        return "", 200

    parsed_message = parse_whatsapp_message(body)
    if parsed_message is None:
        return "", 200

    phone, text = parsed_message

    # --- pending confirmation: the user is answering "which store?" ---
    if has_pending_items(phone):
        _resolve_pending_confirmation(phone, text)
        return "", 200

    # --- parse the message ---
    try:
        store, raw_items = parse_whatsapp_text(text)
    except ValueError as exc:
        _reply_or_log(phone, f"Sorry, {exc}. {WHATSAPP_FORMAT_HINT}")
        return "", 200

    if store:
        # Explicit store included — add directly
        _execute_add(phone, store, raw_items)
        return "", 200

    # Items only — consult session memory
    fresh_store = get_last_store(phone)
    if fresh_store:
        _reply_or_log(phone, f"Using {fresh_store.title()} (last used store)…")
        _execute_add(phone, fresh_store, raw_items)
        return "", 200

    expired_store = get_expired_store(phone)
    if expired_store:
        set_pending_items(phone, raw_items)
        _reply_or_log(
            phone,
            f"Your {expired_store.title()} session expired. "
            f'Reply "yes" or "{expired_store.title()}" to add there, '
            "or say a different store name.",
        )
        return "", 200

    # No store context at all
    _reply_or_log(phone, f"I need a store name. {WHATSAPP_FORMAT_HINT}")
    return "", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
