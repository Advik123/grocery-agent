# whatsapp.py
# Handles WhatsApp Cloud API payload parsing and outbound replies.
# Phase 3: ngrok exposes the webhook publicly for Meta callback delivery.

import os

import requests  # type: ignore[import-untyped]

GRAPH_API_VERSION = "v19.0"


def parse_whatsapp_message(body: dict) -> tuple[str, str] | None:
    """Extract sender and text from a standard WhatsApp user message payload."""
    entries = body.get("entry")
    if not isinstance(entries, list):
        return None

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            messages = value.get("messages")
            if not isinstance(messages, list):
                continue

            for message in messages:
                if not isinstance(message, dict):
                    continue

                phone_number = message.get("from")
                text = message.get("text")
                if not isinstance(text, dict):
                    continue

                message_text = text.get("body")
                if isinstance(phone_number, str) and isinstance(message_text, str):
                    return phone_number, message_text

    return None


def send_whatsapp_reply(to: str, message: str) -> None:
    """Send *message* to *to* through the WhatsApp Cloud API."""
    phone_number_id = os.getenv("WA_PHONE_NUMBER_ID")
    access_token = os.getenv("WA_ACCESS_TOKEN")
    if not phone_number_id or not access_token:
        raise RuntimeError("WhatsApp Cloud API credentials are not configured")

    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
    except requests.RequestException as exc:
        raise RuntimeError(f"WhatsApp API request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"WhatsApp API returned {response.status_code}: {response.text}"
        )
