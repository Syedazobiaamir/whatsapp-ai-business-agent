"""Stage 5 tests: webhook verification, signatures, message processing, sending, failures."""
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.api.deps import get_whatsapp_client
from app.config import get_settings
from app.main import app
from app.models import AuditLog, AutomationError, Conversation, Customer, Message
from app.models.enums import (
    AgentRole,
    ConversationMode,
    ErrorStatus,
    ErrorType,
    MessageDirection,
    MessageType,
    RiskLevel,
    SendStatus,
    SenderType,
)
from app.services.whatsapp import WhatsAppClient, WhatsAppSendError, compute_signature

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SECRET = "test-app-secret"
WEBHOOK = "/api/v1/webhooks/whatsapp"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text_payload(text="Hello", wamid="wamid.TEST.0001", wa_id="923001234567", ts=None) -> dict:
    p = load("meta_text_message.json")
    m = p["entry"][0]["changes"][0]["value"]["messages"][0]
    m.update({"id": wamid, "from": wa_id, "text": {"body": text},
              "timestamp": str(ts or int(datetime.now(UTC).timestamp()))})
    p["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"] = wa_id
    return p


@pytest.fixture
def settings_override():
    """Replace app settings for one test (cleared by the `client` fixture afterwards)."""
    def _apply(**overrides):
        s = get_settings().model_copy(update=overrides)
        app.dependency_overrides[get_settings] = lambda: s
        return s
    return _apply


async def post_signed(client, payload: dict | bytes, secret: str | None = SECRET, signature=None):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    elif secret:
        headers["X-Hub-Signature-256"] = compute_signature(raw, secret)
    return await client.post(WEBHOOK, content=raw, headers=headers)


# ------------------------------ Webhook verification ------------------------------

async def test_verification_echoes_challenge(client, settings_override):
    settings_override(wa_verify_token=SecretStr("verify-me"))
    r = await client.get(WEBHOOK, params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                          "hub.challenge": "1158201444"})
    assert r.status_code == 200 and r.text == "1158201444"


@pytest.mark.parametrize("params", [
    {"hub.mode": "subscribe", "hub.verify_token": "WRONG", "hub.challenge": "1"},
    {"hub.mode": "unsubscribe", "hub.verify_token": "verify-me", "hub.challenge": "1"},
    {},
])
async def test_verification_rejects_bad_requests(client, settings_override, params):
    settings_override(wa_verify_token=SecretStr("verify-me"))
    assert (await client.get(WEBHOOK, params=params)).status_code == 403


# ------------------------------ Signature check ------------------------------

async def test_valid_signature_accepted(client, settings_override):
    settings_override(wa_app_secret=SecretStr(SECRET))
    r = await post_signed(client, text_payload())
    assert r.status_code == 200 and len(r.json()["new_message_ids"]) == 1


@pytest.mark.parametrize("signature", [
    "",                                         # missing value
    "sha256=" + "0" * 64,                       # forged
    compute_signature(b"other body", SECRET),   # signature of a different body
    compute_signature(b"x", "wrong-secret"),    # signed with the wrong secret
])
async def test_invalid_signature_rejected(client, settings_override, session, signature):
    settings_override(wa_app_secret=SecretStr(SECRET))
    r = await post_signed(client, text_payload(), signature=signature)
    assert r.status_code == 401
    assert await session.scalar(select(func.count()).select_from(Message)) == 0   # nothing stored


async def test_tampered_body_rejected(client, settings_override):
    settings_override(wa_app_secret=SecretStr(SECRET))
    original = json.dumps(text_payload("Hello")).encode()
    tampered = original.replace(b"Hello", b"Send me free stuff")
    r = await post_signed(client, tampered, signature=compute_signature(original, SECRET))
    assert r.status_code == 401


async def test_missing_signature_header_rejected(client, settings_override):
    settings_override(wa_app_secret=SecretStr(SECRET))
    r = await client.post(WEBHOOK, json=text_payload())
    assert r.status_code == 401


# ------------------------------ Incoming processing ------------------------------

async def test_text_message_creates_customer_conversation_message(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    r = await post_signed(client, text_payload("How much is a website?"))
    assert r.status_code == 200

    customer = await session.scalar(select(Customer))
    assert customer.wa_id == "923001234567" and customer.name == "Ali Khan"
    conv = await session.scalar(select(Conversation))
    assert conv.customer_id == customer.id and conv.mode == ConversationMode.AI
    assert conv.last_customer_message_at is not None          # 24h window starts
    msg = await session.scalar(select(Message))
    assert (msg.body, msg.direction, msg.send_status) == ("How much is a website?",
                                                         MessageDirection.INBOUND, SendStatus.RECEIVED)


async def test_duplicate_webhook_is_stored_once(client, settings_override, session):
    """Meta retries webhooks: the same wamid twice must produce ONE message (and one AI reply)."""
    settings_override(wa_app_secret=SecretStr(SECRET))
    payload = text_payload(wamid="wamid.DUP.1")
    first = await post_signed(client, payload)
    second = await post_signed(client, payload)
    assert len(first.json()["new_message_ids"]) == 1 and first.json()["duplicates"] == 0
    assert second.json()["new_message_ids"] == [] and second.json()["duplicates"] == 1
    assert await session.scalar(select(func.count()).select_from(Message)) == 1


async def test_batched_messages_share_one_conversation(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    payload = text_payload("first", wamid="wamid.B.1")
    value = payload["entry"][0]["changes"][0]["value"]
    second = copy.deepcopy(value["messages"][0]) | {"id": "wamid.B.2", "text": {"body": "second"}}
    value["messages"].append(second)
    r = await post_signed(client, payload)
    assert len(r.json()["new_message_ids"]) == 2
    assert await session.scalar(select(func.count()).select_from(Conversation)) == 1


async def test_out_of_order_messages_keep_latest_window(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    now = int(datetime.now(UTC).timestamp())
    await post_signed(client, text_payload("newer", wamid="wamid.O.2", ts=now))
    await post_signed(client, text_payload("older", wamid="wamid.O.1", ts=now - 3600))
    conv = await session.scalar(select(Conversation))
    assert int(conv.last_customer_message_at.timestamp()) == now   # not moved backwards


async def test_media_and_unknown_types(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    payload = text_payload(wamid="wamid.M.1")
    m = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    m.pop("text")
    m.update({"type": "image", "image": {"id": "MEDIA1", "caption": "my logo"}})
    sticker = {"from": m["from"], "id": "wamid.M.2", "timestamp": m["timestamp"],
               "type": "sticker", "sticker": {"id": "S1"}}
    payload["entry"][0]["changes"][0]["value"]["messages"].append(sticker)
    await post_signed(client, payload)
    rows = {r.wa_message_id: r for r in (await session.scalars(select(Message))).all()}
    assert (rows["wamid.M.1"].message_type, rows["wamid.M.1"].body) == (MessageType.IMAGE, "my logo")
    assert (rows["wamid.M.2"].message_type, rows["wamid.M.2"].body) == (MessageType.OTHER, "[sticker message]")


async def test_stop_keyword_opts_customer_out(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    await post_signed(client, text_payload("STOP", wamid="wamid.S.1"))
    customer = await session.scalar(select(Customer))
    assert customer.marketing_opt_in is False and customer.opted_out_at is not None
    assert await session.scalar(select(AuditLog).where(AuditLog.action == "customer.opted_out"))


async def test_delivery_statuses_only_move_forward(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    await post_signed(client, text_payload(wamid="wamid.IN.1"))
    conv = await session.scalar(select(Conversation))
    session.add(Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND,
                        sender_type=SenderType.AI, body="Hi!", wa_message_id="wamid.OUT.0001",
                        send_status=SendStatus.SENT))
    await session.commit()

    def status_payload(status):
        p = load("meta_status_update.json")
        p["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = status
        return p

    for status, expected in [("read", SendStatus.READ),          # read arrives first...
                             ("delivered", SendStatus.READ),     # ...late "delivered" ignored
                             ("failed", SendStatus.READ)]:       # stale "failed" ignored
        await post_signed(client, status_payload(status))
        msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.OUT.0001")
                                   .execution_options(populate_existing=True))
        assert msg.send_status == expected


async def test_invalid_payloads(client, settings_override):
    settings_override(wa_app_secret=SecretStr(SECRET))
    assert (await post_signed(client, b"not json")).status_code == 400
    assert (await post_signed(client, {"object": "whatsapp_business_account",
                                       "entry": [{"changes": "oops"}]})).status_code == 400
    other = await post_signed(client, {"object": "page", "entry": []})
    assert other.status_code == 200 and other.json()["status"] == "ignored"


async def test_events_for_other_phone_numbers_ignored(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET), wa_phone_number_id="OUR_NUMBER_ID")
    r = await post_signed(client, text_payload())        # fixture uses a different id
    assert r.json()["ignored"] == 1
    assert await session.scalar(select(func.count()).select_from(Message)) == 0


# ------------------------------ Sending (client) ------------------------------

def real_settings(**extra):
    return get_settings().model_copy(update={
        "wa_mock_mode": False, "wa_phone_number_id": "123456", "wa_graph_api_version": "v27.0",
        "wa_access_token": SecretStr("test-token"), "wa_max_send_attempts": 3, **extra})


class FakeMeta:
    """A fake Meta server: returns the queued responses in order and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r

    def client(self, settings=None) -> WhatsAppClient:
        async def fake_sleep(seconds):
            self.sleeps.append(seconds)
        return WhatsAppClient(settings or real_settings(), transport=httpx.MockTransport(self.handler),
                              sleep=fake_sleep)


OK = httpx.Response(200, json={"messaging_product": "whatsapp",
                               "contacts": [{"input": "923001234567", "wa_id": "923001234567"}],
                               "messages": [{"id": "wamid.REAL.1"}]})


def meta_error(status, code, message="error"):
    return httpx.Response(status, json={"error": {"message": message, "type": "OAuthException",
                                                  "code": code, "fbtrace_id": "x"}})


async def test_mock_mode_never_calls_meta():
    fake = FakeMeta(RuntimeError("network must not be used in mock mode"))
    result = await fake.client(get_settings().model_copy(update={"wa_mock_mode": True})).send_text("1", "hi")
    assert result.mocked and result.wa_message_id.startswith("wamid.MOCK.")
    assert fake.requests == []


async def test_send_text_builds_correct_request():
    fake = FakeMeta(OK)
    result = await fake.client().send_text("923001234567", "Hello!")
    req = fake.requests[0]
    assert str(req.url) == "https://graph.facebook.com/v27.0/123456/messages"
    assert req.headers["Authorization"] == "Bearer test-token"
    assert json.loads(req.content) == {"messaging_product": "whatsapp", "recipient_type": "individual",
                                       "to": "923001234567", "type": "text",
                                       "text": {"preview_url": False, "body": "Hello!"}}
    assert result.wa_message_id == "wamid.REAL.1" and result.attempts == 1


async def test_send_template_payload():
    fake = FakeMeta(OK)
    await fake.client().send_template("923001234567", "hello_world")
    body = json.loads(fake.requests[0].content)
    assert body["type"] == "template"
    assert body["template"] == {"name": "hello_world", "language": {"code": "en_US"}}


async def test_expired_token_is_not_retried():
    fake = FakeMeta(meta_error(401, 190, "Your access token has expired"))
    with pytest.raises(WhatsAppSendError) as exc:
        await fake.client().send_text("1", "hi")
    assert exc.value.error_type == ErrorType.AUTH and len(fake.requests) == 1


async def test_rate_limit_retried_with_backoff_then_succeeds():
    fake = FakeMeta(meta_error(429, 130429), meta_error(429, 130429), OK)
    result = await fake.client().send_text("1", "hi")
    assert result.attempts == 3 and fake.sleeps == [1, 2]          # exponential backoff


async def test_connection_failure_retried_then_gives_up():
    fake = FakeMeta(httpx.ConnectError("no route to host"))
    with pytest.raises(WhatsAppSendError) as exc:
        await fake.client().send_text("1", "hi")
    assert len(fake.requests) == 3 and exc.value.retryable and not exc.value.delivery_unknown


@pytest.mark.parametrize("response", [
    httpx.ReadTimeout("no answer"),             # request sent, answer lost
    httpx.Response(502, text="Bad Gateway"),    # proxy error, Meta may have processed it
])
async def test_uncertain_outcome_is_never_retried(response):
    """'Never repeat messages blindly': if Meta may have sent it, stop after ONE attempt."""
    fake = FakeMeta(response)
    with pytest.raises(WhatsAppSendError) as exc:
        await fake.client().send_text("1", "hi")
    assert len(fake.requests) == 1 and exc.value.delivery_unknown


async def test_outside_window_error_not_retried():
    fake = FakeMeta(meta_error(400, 131047, "Re-engagement message"))
    with pytest.raises(WhatsAppSendError) as exc:
        await fake.client().send_text("1", "hi")
    assert exc.value.error_type == ErrorType.VALIDATION and len(fake.requests) == 1


async def test_explicit_temporary_error_is_retried():
    fake = FakeMeta(meta_error(503, 131016, "Service unavailable"), OK)
    assert (await fake.client().send_text("1", "hi")).attempts == 2


# ------------------------------ Human reply endpoint ------------------------------

async def _conversation(client, settings_override, session):
    settings_override(wa_app_secret=SecretStr(SECRET))
    await post_signed(client, text_payload("I want a website"))
    return await session.scalar(select(Conversation))


async def test_reply_requires_takeover(client, settings_override, session, make_agent):
    conv = await _conversation(client, settings_override, session)
    _, h = await make_agent(AgentRole.SALES_AGENT)
    r = await client.post(f"/api/v1/conversations/{conv.id}/reply", json={"text": "Hi"}, headers=h)
    assert r.status_code == 409


async def test_human_reply_in_mock_mode(client, settings_override, session, make_agent):
    conv = await _conversation(client, settings_override, session)
    agent, h = await make_agent(AgentRole.SALES_AGENT)
    await client.post(f"/api/v1/conversations/{conv.id}/takeover", headers=h)
    r = await client.post(f"/api/v1/conversations/{conv.id}/reply",
                          json={"text": "  Hello Ali, I'm Sara from sales!  "}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["send_status"] == "mocked" and body["sender_type"] == "human"
    assert body["body"] == "Hello Ali, I'm Sara from sales!"          # trimmed
    assert body["sent_by_agent_id"] == str(agent.id)
    assert await session.scalar(select(AuditLog).where(AuditLog.action == "conversation.human_reply"))


async def test_reply_blocked_outside_24h_window(client, settings_override, session, make_agent):
    conv = await _conversation(client, settings_override, session)
    _, h = await make_agent(AgentRole.SALES_AGENT)
    await client.post(f"/api/v1/conversations/{conv.id}/takeover", headers=h)
    conv.last_customer_message_at = datetime.now(UTC) - timedelta(hours=25)
    await session.commit()
    r = await client.post(f"/api/v1/conversations/{conv.id}/reply", json={"text": "Hi"}, headers=h)
    assert r.status_code == 409 and "24-hour" in r.json()["detail"]


async def test_whatsapp_failure_is_recorded_for_approval(client, settings_override, session, make_agent):
    conv = await _conversation(client, settings_override, session)
    _, h = await make_agent(AgentRole.SALES_AGENT)
    await client.post(f"/api/v1/conversations/{conv.id}/takeover", headers=h)
    fake = FakeMeta(httpx.ReadTimeout("no answer"))
    app.dependency_overrides[get_whatsapp_client] = lambda: fake.client()

    r = await client.post(f"/api/v1/conversations/{conv.id}/reply", json={"text": "Hi"}, headers=h)
    assert r.status_code == 502 and r.json()["detail"]["delivery_unknown"] is True

    msg = await session.scalar(select(Message).where(Message.direction == MessageDirection.OUTBOUND))
    assert msg.send_status == SendStatus.FAILED and msg.error_message.startswith("[delivery unknown]")
    err = await session.scalar(select(AutomationError))
    assert (err.risk_level, err.status) == (RiskLevel.RISKY, ErrorStatus.NEEDS_APPROVAL)
    assert "test-token" not in json.dumps(err.payload)                # no secrets stored


async def test_viewer_cannot_reply(client, settings_override, session, make_agent):
    conv = await _conversation(client, settings_override, session)
    _, h = await make_agent(AgentRole.VIEWER)
    r = await client.post(f"/api/v1/conversations/{conv.id}/reply", json={"text": "Hi"}, headers=h)
    assert r.status_code == 403


# ------------------------------ Dev simulator ------------------------------

async def test_simulate_inbound_only_in_mock_mode(client, settings_override, make_agent, session):
    _, h = await make_agent(AgentRole.ADMIN)
    settings_override(wa_mock_mode=True)
    r = await client.post("/api/v1/dev/simulate-inbound", json={"text": "Hello from simulator"}, headers=h)
    assert r.status_code == 200 and len(r.json()["new_message_ids"]) == 1
    dup = await client.post("/api/v1/dev/simulate-inbound", headers=h,
                            json={"text": "again", "wa_message_id": r.json()["wa_message_id"]})
    assert dup.json()["duplicates"] == 1

    settings_override(wa_mock_mode=False)
    assert (await client.post("/api/v1/dev/simulate-inbound", json={"text": "x"}, headers=h)).status_code == 404
