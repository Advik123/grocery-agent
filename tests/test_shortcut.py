from unittest.mock import patch

import pytest

import app as app_module
from conversation_memory import _SESSIONS


@pytest.fixture
def client(monkeypatch):
    _SESSIONS.clear()
    monkeypatch.setenv("SHORTCUT_TOKEN", "secret-token")
    monkeypatch.delenv("SHORTCUT_SENDER_ID", raising=False)
    return app_module.app.test_client()


def _post(client, token="secret-token", **kwargs):
    headers = {"X-Token": token} if token is not None else {}
    return client.post("/shortcut", headers=headers, **kwargs)


def test_unset_token_returns_503(client, monkeypatch):
    monkeypatch.delenv("SHORTCUT_TOKEN")
    assert _post(client, json={"text": "eggs"}).status_code == 503


def test_missing_token_returns_401(client):
    assert _post(client, token=None, json={"text": "eggs"}).status_code == 401


def test_wrong_token_returns_401(client):
    assert _post(client, token="nope", json={"text": "eggs"}).status_code == 401


def test_empty_text_returns_400(client):
    assert _post(client, json={"text": "   "}).status_code == 400


@patch("app.send_whatsapp_reply")
@patch("app.add_to_list")
@patch("app.parse_whatsapp_text")
def test_happy_path_returns_reply(mock_parse, mock_add, mock_send, client):
    mock_parse.return_value = ("aldi", {"eggs": 3})

    async def fake_add(url, items):
        return ["eggs x3"]

    mock_add.side_effect = fake_add
    resp = _post(client, json={"text": "3 eggs to aldi"})
    assert resp.status_code == 200
    assert resp.get_json() == {"reply": "Added: eggs x3 to Aldi ✓"}
    mock_send.assert_not_called()


@patch("app.send_whatsapp_reply")
@patch("app.parse_whatsapp_text")
def test_plain_text_body_and_memory_prompt(mock_parse, mock_send, client):
    mock_parse.return_value = (None, {"eggs": 1})
    resp = _post(client, data="eggs", content_type="text/plain")
    assert resp.status_code == 200
    assert "I need a store name" in resp.get_json()["reply"]
    mock_send.assert_not_called()
