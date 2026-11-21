# message_parser.py
# Parses WhatsApp grocery commands into the existing store/items payload shape.
# Simple messages ("milk, eggs at aldi") take a regex fast path with no LLM call;
# anything else goes to Gemini (gemini-2.5-flash by default, thinking disabled).
# Phase 3: Siri App Intents can reuse the same structured item format.

import functools
import json
import os
import re
import time

from google import genai
from google.genai import types

from store_config import STORE_LISTS

_DEFAULT_MODEL = "gemini-2.5-flash"

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

# --- fast path -------------------------------------------------------------

# An item that is one of these words means the message is more than a plain list
# (a sentence, a negation, a unit, a spelled-out quantity or a chat reply), so
# Gemini decides.
_AMBIGUOUS_WORDS = frozenset(
    {
        "a", "add", "all", "also", "an", "and", "any", "are", "at", "bag", "bags",
        "bottle", "bottles", "box", "boxes", "but", "buy", "can", "cancel",
        "could", "couple", "delete", "dont", "don't", "dozen", "eight", "eleven",
        "except", "few", "five", "for", "four", "from", "g", "gallon", "gallons",
        "get", "grab", "half", "i", "in", "instead", "is", "it", "just", "kg",
        "lb", "lbs", "less", "list", "liter", "liters", "litre", "litres", "lot",
        "lots", "many", "me", "ml", "more", "much", "my", "need", "needs", "nine",
        "no", "not", "nothing", "of", "on", "one", "only", "or", "our", "ounce",
        "ounces", "oz", "pack", "packs", "pick", "please", "pound", "pounds",
        "put", "remove", "seven", "several", "six", "some", "store", "ten",
        "that", "the", "them", "these", "this", "those", "three", "to", "too",
        "twelve", "two", "undo", "want", "we", "will", "with", "without",
        "would", "you", "your",
        # chat replies and greetings, which are not grocery items
        "done", "hello", "help", "hey", "hi", "how", "nah", "nope", "ok", "okay",
        "stop", "sure", "test", "thank", "thanks", "thx", "ty", "what", "when",
        "where", "who", "why", "y", "yeah", "yep", "yes", "yup",
    }
)  # fmt: skip

# "X and Y" pairs that are usually one product, so splitting on "and" is unsafe.
_AND_COMPOUNDS = frozenset(
    {
        ("biscuits", "gravy"), ("bread", "butter"), ("butter", "jelly"),
        ("chips", "salsa"), ("cookies", "cream"), ("cream", "onion"),
        ("fish", "chips"), ("franks", "beans"), ("ham", "cheese"),
        ("mac", "cheese"), ("macaroni", "cheese"), ("oil", "vinegar"),
        ("pork", "beans"), ("rice", "beans"), ("salt", "pepper"),
        ("sweet", "sour"),
    }
)  # fmt: skip

# Mass nouns whose "-s" plural is always the same product ("milks" -> "milk");
# every other name is kept as typed so "eggs" stays "eggs".
_MASS_NOUNS = frozenset(
    {
        "bread", "butter", "cheese", "coffee", "cream", "flour", "honey",
        "juice", "milk", "rice", "salt", "sugar", "tea", "water", "yogurt",
    }
)  # fmt: skip

# Names are a single word: without punctuation, "eggs milk bread" could be one
# item or three, so multi-word names are left to Gemini.
_QTY = r"(?P<qty>[1-9]\d?)"
_NAME = r"(?P<name>(?!x\b)[a-z][a-z'-]*)"
_ITEM_PATTERNS = (
    re.compile(rf"{_QTY}(?:\s*x)?\s+{_NAME}"),  # "2 eggs", "2x eggs"
    re.compile(rf"{_NAME}(?:\s+x\s*|\s+){_QTY}"),  # "eggs 3", "eggs x3"
    re.compile(_NAME),  # "eggs"
)
_LEADING_VERB = re.compile(r"(?:please\s+)?(?:add|get|buy)\s+")


def _singular(name: str) -> str:
    """Return *name* with a mass-noun plural made singular; otherwise unchanged."""
    for suffix in ("es", "s"):
        stem = name.removesuffix(suffix)
        if stem != name and stem in _MASS_NOUNS:
            return stem
    return name


def _parse_item(chunk: str) -> tuple[str, int] | None:
    """Parse one list *chunk* like "2 eggs" or "eggs x3", or None if unclear."""
    for pattern in _ITEM_PATTERNS:
        match = pattern.fullmatch(chunk)
        if match is None:
            continue
        name = match["name"]
        if name in _AMBIGUOUS_WORDS or name in STORE_LISTS:
            return None
        qty = int(match["qty"]) if "qty" in pattern.groupindex else 1
        return _singular(name), qty
    return None


def _parse_item_list(text: str) -> dict[str, int] | None:
    """Parse comma / "and" separated *text* into items, or None if unclear."""
    words = text.replace(",", " ").split()
    for left, mid, right in zip(words, words[1:], words[2:], strict=False):
        if mid in ("and", "&") and (left, right) in _AND_COMPOUNDS:
            return None

    items: dict[str, int] = {}
    for part in text.split(","):
        part = part.strip().removeprefix("and ")
        for chunk in re.split(r"\s+(?:and|&)\s+", part):
            parsed = _parse_item(chunk.strip())
            if parsed is None or parsed[0] in items:
                return None
            items[parsed[0]] = parsed[1]
    return items


def _fast_parse(text: str) -> tuple[str | None, dict[str, int]] | None:
    """Parse an unambiguous simple list in *text* without an LLM call.

    Returns None whenever Gemini should decide instead.  Raises ValueError when
    the message is only a known store name (matching Gemini's "no items" error).
    """
    t = " ".join(text.lower().replace("\n", ",").split()).rstrip(".!")
    verb = _LEADING_VERB.match(t)
    if verb:
        t = t[verb.end() :]
    if not t:
        return None

    stores = "|".join(re.escape(s) for s in sorted(STORE_LISTS, key=len, reverse=True))
    if re.fullmatch(rf"(?:the\s+)?(?:{stores})(?:\s+list)?", t):
        raise ValueError("no items found")

    store: str | None = None
    body = t
    match = re.fullmatch(rf"(?P<store>{stores})\s*:\s*(?P<items>.+)", t) or (
        re.fullmatch(
            rf"(?P<items>.+?),?\s+(?:(?:at|to|for|from)\s+)?(?:the\s+)?"
            rf"(?P<store>{stores})(?:\s+list)?",
            t,
        )
    )
    if match is not None:
        store, body = match["store"], match["items"]

    items = _parse_item_list(body)
    if not items:
        return None
    return store, items


# --- Gemini ----------------------------------------------------------------


def _thinking_config(model: str) -> types.ThinkingConfig | None:
    """Return the lowest-latency thinking setting the *model* family accepts."""
    if model.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=0)
    if re.match(r"gemini-[3-9]", model):
        # Gemini 3+ cannot turn thinking off and rejects thinking_budget=0.
        return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
    return None


@functools.cache
def _gemini() -> tuple[genai.Client, str, types.GenerateContentConfig]:
    """Build the Gemini client, model name and request config once per process."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini API call failed: GEMINI_API_KEY is not set")
    model = os.getenv("GEMINI_MODEL") or _DEFAULT_MODEL
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        response_mime_type="application/json",
        thinking_config=_thinking_config(model),
    )
    return genai.Client(api_key=api_key), model, config


def _gemini_parse(text: str) -> tuple[str | None, dict[str, int]]:
    """Parse *text* with Gemini; see parse_whatsapp_text for the contract."""
    client, model, config = _gemini()
    start = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model, contents=text, config=config
        )
        raw = (response.text or "").strip()
    except Exception as exc:
        raise RuntimeError(f"Gemini API call failed: {exc}") from exc
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[timing] gemini_parse {elapsed_ms:.0f} ms ({model})", flush=True)

    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned unparseable JSON: {raw!r}") from exc

    if "error" in data:
        raise ValueError(data["error"])

    store: str | None = data.get("store") or None
    items: dict[str, int] = data.get("items", {})

    return store, items


def parse_whatsapp_text(text: str) -> tuple[str | None, dict[str, int]]:
    """Parse WhatsApp *text* into an optional store name and item quantity mapping.

    Simple lists are parsed locally; everything else is sent to Gemini.  Returns
    *store* as None when no store is identifiable in the message, so the caller
    can apply session memory to fill it in.  Raises ValueError only when no
    items are found.  Raises RuntimeError on Gemini API or JSON failures.
    """
    start = time.perf_counter()
    fast = _fast_parse(text)
    if fast is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[timing] fast_path_parse {elapsed_ms:.1f} ms", flush=True)
        return fast
    return _gemini_parse(text)
