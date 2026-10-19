import hashlib
import hmac
import json
from datetime import date, timedelta

import httpx
from pydantic import SecretStr

from app.core.config import settings
from app.services.agent import LLMClient
from app.services.whatsapp import WhatsAppClient, extract_messages, verify_signature
from tests.conftest import D1, register


def fake_llm(tool_args=None, seen=None):
    """First turn: call search_transport. After the tool result: write the final answer."""
    args = tool_args or {"kind": "flight", "origin": "Delhi", "destination": "Goa", "date": D1}

    def handler(req):
        body = json.loads(req.content)
        if seen is not None:
            seen.append(body["messages"])
        if body["messages"][-1]["role"] == "tool":
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Here are some options."}}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search_transport", "arguments": json.dumps(args)}}]}}]})

    return LLMClient("http://llm.test/v1", "m", None, 5, transport=httpx.MockTransport(handler))


def test_agent_searches_and_returns_cards_with_links(client):
    h = register(client)
    seen = []
    client.app.state.llm = fake_llm(seen=seen)
    r = client.post("/v1/agent/chat", headers=h, json={"message": "flights Delhi to Goa"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Here are some options." and 1 <= len(body["cards"]) <= 5
    card = body["cards"][0]
    assert card["offer"]["offer_id"] in card["link"] and card["link"].startswith(settings.web_base_url)
    tool_msg = json.loads(seen[-1][-1]["content"])  # what the model saw
    assert tool_msg["results"][0]["option"] == 1
    assert "offer_id" not in seen[-1][-1]["content"]  # model never handles ids or links
    # history is remembered across turns, tool traffic is not
    client.post("/v1/agent/chat", headers=h, json={"message": "cheaper?"})
    assert [m["role"] for m in seen[-2]] == ["system", "user", "assistant", "user"]  # turn 2, first model call


def test_agent_tool_errors_go_back_to_the_model(client):
    h = register(client)
    seen = []
    past = (date.today() - timedelta(days=3)).isoformat()
    client.app.state.llm = fake_llm({"kind": "flight", "origin": "Delhi", "destination": "Goa", "date": past}, seen)
    r = client.post("/v1/agent/chat", headers=h, json={"message": "flights"})
    assert r.status_code == 200 and r.json()["cards"] == []
    assert "past" in json.loads(seen[-1][-1]["content"])["error"]


def test_agent_unavailable_without_llm_and_requires_auth(client):
    assert client.post("/v1/agent/chat", json={"message": "hi"}).status_code == 401
    h = register(client)
    r = client.post("/v1/agent/chat", headers=h, json={"message": "hi"})
    assert r.status_code == 503 and r.json()["code"] == "agent_unavailable"


# ---- WhatsApp ---------------------------------------------------------------

SECRET = "app-secret"


def wa_setup(client, monkeypatch, sent):
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr("verify-me"))
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("tok"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123")

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={})

    client.app.state.whatsapp = WhatsAppClient(settings, transport=httpx.MockTransport(handler))
    client.app.state.llm = fake_llm()


def payload(text="flights", msg_id="wamid.1", typ="text"):
    m = {"id": msg_id, "from": "919999999999", "type": typ}
    if typ == "text":
        m["text"] = {"body": text}
    return {"entry": [{"changes": [{"value": {"messages": [m]}}]}]}


def post(client, data, secret=SECRET):
    raw = json.dumps(data).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/v1/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def test_whatsapp_handshake(client, monkeypatch):
    wa_setup(client, monkeypatch, [])
    ok = client.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "4242"})
    assert ok.status_code == 200 and ok.text == "4242"
    assert client.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}).status_code == 403


def test_whatsapp_rejects_bad_signature(client, monkeypatch):
    sent = []
    wa_setup(client, monkeypatch, sent)
    assert post(client, payload(), secret="wrong").status_code == 403
    assert client.post("/v1/whatsapp/webhook", json=payload()).status_code == 403  # missing header
    assert sent == []


def test_whatsapp_message_gets_agent_reply_with_book_links(client, monkeypatch):
    sent = []
    wa_setup(client, monkeypatch, sent)
    assert post(client, payload()).status_code == 200
    assert len(sent) == 1 and sent[0]["to"] == "919999999999"
    text = sent[0]["text"]["body"]
    assert "Here are some options." in text and "Book: " + settings.web_base_url in text and "1)" in text


def test_whatsapp_redelivery_is_not_answered_twice(client, monkeypatch):
    sent = []
    wa_setup(client, monkeypatch, sent)
    post(client, payload(msg_id="wamid.dup"))
    post(client, payload(msg_id="wamid.dup"))
    assert len(sent) == 1


def test_whatsapp_non_text_and_throttle(client, monkeypatch):
    sent = []
    wa_setup(client, monkeypatch, sent)
    post(client, payload(typ="image", msg_id="wamid.img"))
    assert "only read text" in sent[0]["text"]["body"]
    wa = client.app.state.whatsapp
    assert all(wa.allow("x") for _ in range(10)) and not wa.allow("x")


def test_signature_helpers():
    raw = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", raw, hashlib.sha256).hexdigest()
    assert verify_signature("s", raw, good) and not verify_signature("s", raw, "sha256=00") and not verify_signature("s", raw, None)
    assert extract_messages({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []
