# Tests for token auth on /add and /status and signature checks on /whatsapp.

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

import app as app_module
from conversation_memory import _SESSIONS

SECRET_VALUES = {
    "ADD_TOKEN": "add-secret-value",
    "STATUS_TOKEN": "status-secret-value",
    "WA_APP_SECRET": "app-secret-value",
    "WA_VERIFY_TOKEN": "verify-secret-value",
    "WA_ACCESS_TOKEN": "access-secret-value",
    "GEMINI_API_KEY": "gemini-secret-value",
}


@pytest.fixture
def client(monkeypatch):
    _SESSIONS.clear()
    for name, value in SECRET_VALUES.items():
        monkeypatch.setenv(name, value)
    return app_module.app.test_client()


def _sign(body: bytes, secret: str = "app-secret-value") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


ADD_BODY = {"store": "aldi", "items": {"eggs": 2}}


def test_add_unset_token_503(client, monkeypatch):
    monkeypatch.delenv("ADD_TOKEN")
    assert client.post("/add", json=ADD_BODY).status_code == 503


def test_add_missing_token_401(client):
    assert client.post("/add", json=ADD_BODY).status_code == 401


def test_add_bad_token_401(client):
    resp = client.post("/add", json=ADD_BODY, headers={"X-Token": "nope"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Token": "add-secret-value"},
        {"Authorization": "Bearer add-secret-value"},
    ],
)
def test_add_good_token_passes(client, headers):
    async def fake_add(url, items):
        return ["eggs x2"]

    with patch("app.add_to_list", side_effect=fake_add):
        resp = client.post("/add", json=ADD_BODY, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["added"] == ["eggs x2"]


def test_add_good_token_still_validates(client):
    resp = client.post("/add", data="x", headers={"X-Token": "add-secret-value"})
    assert resp.status_code == 400


WA_PAYLOAD = json.dumps({"object": "whatsapp_business_account"}).encode()


def _wa_post(client, headers):
    return client.post(
        "/whatsapp",
        data=WA_PAYLOAD,
        content_type="application/json",
        headers=headers,
    )


def test_whatsapp_valid_signature_accepted(client):
    resp = _wa_post(client, {"X-Hub-Signature-256": _sign(WA_PAYLOAD)})
    assert resp.status_code == 200


def test_whatsapp_valid_signature_handles_message(client):
    payload = json.dumps({"x": 1}).encode()
    with (
        patch("app.parse_whatsapp_message", return_value=("1555", "hi")),
        patch("app._handle_message") as handle,
    ):
        resp = client.post(
            "/whatsapp",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(payload)},
        )
    assert resp.status_code == 200
    handle.assert_called_once_with("1555", "hi")


def test_whatsapp_invalid_signature_403(client):
    bad = _sign(WA_PAYLOAD, secret="wrong")
    assert _wa_post(client, {"X-Hub-Signature-256": bad}).status_code == 403


def test_whatsapp_missing_signature_403(client):
    assert _wa_post(client, {}).status_code == 403


def test_whatsapp_unprefixed_signature_403(client):
    sig = _sign(WA_PAYLOAD)[len("sha256=") :]
    assert _wa_post(client, {"X-Hub-Signature-256": sig}).status_code == 403


def test_whatsapp_unset_secret_503(client, monkeypatch, capsys):
    monkeypatch.delenv("WA_APP_SECRET")
    resp = _wa_post(client, {"X-Hub-Signature-256": _sign(WA_PAYLOAD)})
    assert resp.status_code == 503
    assert "WA_APP_SECRET" in capsys.readouterr().out


def test_whatsapp_get_verification_unchanged(client):
    resp = client.get(
        "/whatsapp",
        query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-secret-value",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "abc"


def test_status_unset_token_503(client, monkeypatch):
    monkeypatch.delenv("STATUS_TOKEN")
    assert client.get("/status").status_code == 503


def test_status_missing_and_bad_token_401(client):
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"X-Token": "nope"}).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Token": "status-secret-value"},
        {"Authorization": "Bearer status-secret-value"},
    ],
)
def test_status_good_token(client, headers):
    resp = client.get("/status", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert isinstance(data["uptime_seconds"], int)
    assert data["config"]["ADD_TOKEN"] is True
    assert data["active_sessions"] == 0


def test_status_reports_unset_vars_as_false(client, monkeypatch):
    monkeypatch.delenv("WA_APP_SECRET")
    resp = client.get("/status", headers={"X-Token": "status-secret-value"})
    assert resp.get_json()["config"]["WA_APP_SECRET"] is False


def test_status_leaks_no_secret_values(client):
    resp = client.get("/status", headers={"X-Token": "status-secret-value"})
    body = resp.get_data(as_text=True)
    for value in SECRET_VALUES.values():
        assert value not in body


def test_status_no_external_calls(client):
    with (
        patch("app.add_to_list") as add,
        patch("app.parse_whatsapp_text") as parse,
    ):
        client.get("/status", headers={"X-Token": "status-secret-value"})
    add.assert_not_called()
    parse.assert_not_called()
