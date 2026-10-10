# message_parser.py
# Parses WhatsApp grocery commands into the existing store/items payload shape.
# Uses Gemini 2.5 Flash for natural language understanding.
# Phase 3: Siri App Intents can reuse the same structured item format.

import json
import os

import google.generativeai as genai

_SYSTEM_PROMPT = (
    "You are a grocery list parser. Extract the store name and items from the user's message.\n"
    "Return ONLY a raw JSON object with no markdown, no backticks, and no preamble in this exact shape:\n"
    '{"store": "aldi", "items": {"eggs": 3, "milk": 2}}\n\n'
    "Rules:\n"
    "- Normalise the store name to lowercase\n"
    "- Default quantity to 1 if none is mentioned\n"
    '- Normalise plural item names to singular where obvious (e.g. "milks" -> "milk", but "eggs" stays "eggs")\n'
    '- If no store is identifiable, set store to null: {"store": null, "items": {"eggs": 3}}\n'
    '- If no items are identifiable, return: {"error": "no items found"}\n'
    "- Item quantities must always be positive integers"
)


def parse_whatsapp_text(text: str) -> tuple[str | None, dict[str, int]]:
    """Parse WhatsApp *text* into an optional store name and item quantity mapping via Gemini.

    Returns *store* as None when no store is identifiable in the message, so the
    caller can apply session memory to fill it in.  Raises ValueError only when no
    items are found.  Raises RuntimeError on Gemini API or JSON failures.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=_SYSTEM_PROMPT,
    )

    try:
        response = model.generate_content(text)
        raw = response.text.strip()
    except Exception as exc:
        raise RuntimeError(f"Gemini API call failed: {exc}") from exc

    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned unparseable JSON: {raw!r}") from exc

    if "error" in data:
        raise ValueError(data["error"])

    store: str | None = data.get("store") or None
    items: dict[str, int] = data.get("items", {})

    return store, items
