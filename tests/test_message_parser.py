import json
from unittest.mock import MagicMock, patch

import pytest

from message_parser import parse_whatsapp_text


def _make_response(payload: dict) -> MagicMock:
    """Build a mock Gemini response whose .text is the JSON-serialised *payload*."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(payload)
    return mock_response


@patch("message_parser.genai.GenerativeModel")
def test_natural_language_store_and_items(MockModel):
    MockModel.return_value.generate_content.return_value = _make_response(
        {"store": "aldi", "items": {"eggs": 3, "milk": 2}}
    )
    assert parse_whatsapp_text("Add 3 eggs and 2 milks to Aldi") == (
        "aldi",
        {"eggs": 3, "milk": 2},
    )


@patch("message_parser.genai.GenerativeModel")
def test_quantity_defaults_to_one(MockModel):
    MockModel.return_value.generate_content.return_value = _make_response(
        {"store": "aldi", "items": {"eggs": 1, "bread": 1}}
    )
    store, items = parse_whatsapp_text("aldi needs eggs and bread")
    assert items["eggs"] == 1
    assert items["bread"] == 1


@patch("message_parser.genai.GenerativeModel")
def test_plural_normalisation(MockModel):
    MockModel.return_value.generate_content.return_value = _make_response(
        {"store": "walmart", "items": {"milk": 2}}
    )
    _, items = parse_whatsapp_text("add 2 milks to walmart")
    assert "milk" in items
    assert "milks" not in items


@patch("message_parser.genai.GenerativeModel")
def test_missing_store_returns_none(MockModel):
    """When Gemini cannot identify a store it returns null; parse_whatsapp_text
    should surface that as store=None (not raise) so the caller can apply session
    memory to fill in the store."""
    MockModel.return_value.generate_content.return_value = _make_response(
        {"store": None, "items": {"eggs": 3, "bread": 1}}
    )
    store, items = parse_whatsapp_text("just eggs and bread")
    assert store is None
    assert items == {"eggs": 3, "bread": 1}


@patch("message_parser.genai.GenerativeModel")
def test_missing_items_raises_value_error(MockModel):
    MockModel.return_value.generate_content.return_value = _make_response(
        {"error": "no items found"}
    )
    with pytest.raises(ValueError, match="no items found"):
        parse_whatsapp_text("aldi")


@patch("message_parser.genai.GenerativeModel")
def test_malformed_json_raises_runtime_error(MockModel):
    bad_response = MagicMock()
    bad_response.text = "not valid json {"
    MockModel.return_value.generate_content.return_value = bad_response
    with pytest.raises(RuntimeError, match="unparseable JSON"):
        parse_whatsapp_text("add eggs to aldi")


@patch("message_parser.genai.GenerativeModel")
def test_api_call_failure_raises_runtime_error(MockModel):
    MockModel.return_value.generate_content.side_effect = Exception("network error")
    with pytest.raises(RuntimeError, match="Gemini API call failed"):
        parse_whatsapp_text("add eggs to aldi")


@patch("message_parser.genai.GenerativeModel")
def test_structured_format_still_parses(MockModel):
    MockModel.return_value.generate_content.return_value = _make_response(
        {"store": "aldi", "items": {"eggs": 3, "milk": 2}}
    )
    assert parse_whatsapp_text("aldi: eggs 3, milk 2") == (
        "aldi",
        {"eggs": 3, "milk": 2},
    )
