import json
from unittest.mock import MagicMock, patch

import pytest

import message_parser
from message_parser import parse_whatsapp_text


@pytest.fixture(autouse=True)
def _clear_gemini_cache():
    message_parser._gemini.cache_clear()
    yield
    message_parser._gemini.cache_clear()


@pytest.fixture
def gemini():
    """Patch the cached Gemini client; returns the mock generate_content."""
    client = MagicMock()
    with patch(
        "message_parser._gemini",
        return_value=(client, "gemini-2.5-flash", MagicMock()),
    ):
        yield client.models.generate_content


def _make_response(payload: dict) -> MagicMock:
    """Build a mock Gemini response whose .text is the JSON-serialised *payload*."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(payload)
    return mock_response


# --- Gemini path ------------------------------------------------------------


def test_natural_language_store_and_items(gemini):
    gemini.return_value = _make_response(
        {"store": "aldi", "items": {"eggs": 3, "milk": 2}}
    )
    assert parse_whatsapp_text("Could you put 3 eggs and 2 milks on Aldi") == (
        "aldi",
        {"eggs": 3, "milk": 2},
    )
    gemini.assert_called_once()


def test_quantity_defaults_to_one(gemini):
    gemini.return_value = _make_response(
        {"store": "aldi", "items": {"eggs": 1, "bread": 1}}
    )
    _, items = parse_whatsapp_text("aldi needs eggs and bread")
    assert items["eggs"] == 1
    assert items["bread"] == 1


def test_missing_store_returns_none(gemini):
    """When Gemini cannot identify a store it returns null; parse_whatsapp_text
    should surface that as store=None (not raise) so the caller can apply session
    memory to fill in the store."""
    gemini.return_value = _make_response(
        {"store": None, "items": {"eggs": 3, "bread": 1}}
    )
    store, items = parse_whatsapp_text("just eggs and bread")
    assert store is None
    assert items == {"eggs": 3, "bread": 1}


def test_missing_items_raises_value_error(gemini):
    gemini.return_value = _make_response({"error": "no items found"})
    with pytest.raises(ValueError, match="no items found"):
        parse_whatsapp_text("nothing today thanks")


def test_malformed_json_raises_runtime_error(gemini):
    bad_response = MagicMock()
    bad_response.text = "not valid json {"
    gemini.return_value = bad_response
    with pytest.raises(RuntimeError, match="unparseable JSON"):
        parse_whatsapp_text("could you add eggs to aldi")


def test_empty_response_raises_runtime_error(gemini):
    empty = MagicMock()
    empty.text = None
    gemini.return_value = empty
    with pytest.raises(RuntimeError, match="unparseable JSON"):
        parse_whatsapp_text("could you add eggs to aldi")


def test_api_call_failure_raises_runtime_error(gemini):
    gemini.side_effect = Exception("network error")
    with pytest.raises(RuntimeError, match="Gemini API call failed"):
        parse_whatsapp_text("could you add eggs to aldi")


def test_missing_api_key_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        parse_whatsapp_text("could you add eggs to aldi")


def test_client_is_built_once(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with patch("message_parser.genai.Client") as client_cls:
        generate = client_cls.return_value.models.generate_content
        generate.return_value = _make_response({"store": None, "items": {"x": 1}})
        parse_whatsapp_text("could you add eggs")
        parse_whatsapp_text("could you add milk")
    client_cls.assert_called_once_with(api_key="test-key")
    config = generate.call_args.kwargs["config"]
    assert generate.call_args.kwargs["model"] == "gemini-2.5-flash"
    assert config.response_mime_type == "application/json"
    assert config.thinking_config.thinking_budget == 0
    assert config.system_instruction == message_parser._SYSTEM_PROMPT


def test_thinking_config_per_model_family():
    assert message_parser._thinking_config("gemini-2.5-flash").thinking_budget == 0
    gemini3 = message_parser._thinking_config("gemini-3.1-flash-lite")
    assert gemini3.thinking_budget is None
    assert gemini3.thinking_level == "LOW"
    assert message_parser._thinking_config("gemini-2.0-flash") is None


# --- fast path (no Gemini call) ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("milk, eggs at aldi", ("aldi", {"milk": 1, "eggs": 1})),
        ("2 eggs and milk at aldi", ("aldi", {"eggs": 2, "milk": 1})),
        ("eggs x3 aldi", ("aldi", {"eggs": 3})),
        ("Add 3 eggs and 2 milks to Aldi", ("aldi", {"eggs": 3, "milk": 2})),
        ("add 2 milks to walmart", ("walmart", {"milk": 2})),
        ("aldi: eggs 3, milk 2", ("aldi", {"eggs": 3, "milk": 2})),
        ("eggs x 3 at the costco list.", ("costco", {"eggs": 3})),
        (
            "3x eggs, bread, and milk for costco",
            ("costco", {"eggs": 3, "bread": 1, "milk": 1}),
        ),
        ("eggs\nmilk\nbread", (None, {"eggs": 1, "milk": 1, "bread": 1})),
        ("eggs and 2 milk", (None, {"eggs": 1, "milk": 2})),
    ],
)
def test_fast_path_matches(gemini, text, expected):
    assert parse_whatsapp_text(text) == expected
    gemini.assert_not_called()


def test_fast_path_keeps_store_lowercase_and_quantities_positive(gemini):
    store, items = parse_whatsapp_text("EGGS 12, Milk at ALDI!")
    assert store == "aldi"
    assert items == {"eggs": 12, "milk": 1}
    gemini.assert_not_called()


@pytest.mark.parametrize("text", ["aldi", "The Aldi list", "add costco"])
def test_fast_path_store_only_raises_value_error(gemini, text):
    with pytest.raises(ValueError, match="no items found"):
        parse_whatsapp_text(text)
    gemini.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [
        "mac and cheese at aldi",  # compound product
        "olive oil at aldi",  # multi-word name
        "eggs milk aldi",  # unseparated words
        "eggs at target",  # unknown store
        "i need eggs",  # sentence
        "no eggs at aldi",  # negation
        "remove milk from aldi",  # not an add
        "two eggs at aldi",  # spelled-out quantity
        "0 eggs at aldi",  # non-positive quantity
        "100 eggs",  # implausible quantity
        "2 lbs chicken at aldi",  # unit
        "eggs, eggs at aldi",  # duplicate
        "eggs, milk?",  # question
        "eggs 3 4",  # odd quantity
        "hi",  # chat, not an item
        "yes",  # confirmation, not an item
        "eggs, costco at aldi",  # store name as item
    ],
)
def test_fast_path_defers_to_gemini(gemini, text):
    gemini.return_value = _make_response({"store": "aldi", "items": {"eggs": 1}})
    assert parse_whatsapp_text(text) == ("aldi", {"eggs": 1})
    gemini.assert_called_once()
