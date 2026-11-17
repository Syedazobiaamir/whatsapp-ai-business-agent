"""Stage 10: edge cases, concurrency and abuse attempts."""
import asyncio
import base64
import json

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.api.deps import get_llm_factory
from app.ai.schemas import SupportResult
from app.config import get_settings
from app.main import app
from app.models import Lead, Message
from app.models.enums import AgentRole, MessageDirection
from app.services.whatsapp import compute_signature

SECRET = "edge-secret"
KEY = "test-internal-key-0123456789"


@pytest.fixture
def signed(client):
    s = get_settings().model_copy(update={"wa_app_secret": SecretStr(SECRET), "wa_mock_mode": True,
                                          "backend_internal_api_key": SecretStr(KEY), "wa_phone_number_id": ""})
    app.dependency_overrides[get_settings] = lambda: s

    async def post(payload: dict | bytes):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        return await client.post("/api/v1/webhooks/whatsapp", content=raw,
                                 headers={"X-Hub-Signature-256": compute_signature(raw, SECRET),
                                          "Content-Type": "application/json"})
    return post


def payload(text: str, wamid: str, wa_id="923001234567"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "1"},
        "contacts": [{"profile": {"name": "Test"}, "wa_id": wa_id}],
        "messages": [{"from": wa_id, "id": wamid, "timestamp": "1763280000", "type": "text",
                      "text": {"body": text}}]}}]}]}


async def test_ten_parallel_duplicate_webhooks_store_one_message(signed, session):
    """Meta retries can arrive at the SAME moment: the database, not luck, must prevent duplicates."""
    results = await asyncio.gather(*[signed(payload("Hi", "wamid.RACE")) for _ in range(10)])
    assert all(r.status_code == 200 for r in results)
    assert sum(len(r.json()["new_message_ids"]) for r in results) == 1
    assert await session.scalar(select(func.count()).select_from(Message)) == 1


async def test_parallel_agent_calls_produce_one_reply(signed, client, session):
    class SlowFake:
        model = "fake"

        async def generate_json(self, **_):
            await asyncio.sleep(0.3)               # parallel calls overlap while "Gemini" thinks
            return SupportResult(intent="faq", confidence=0.9, answer="We build websites.",
                                 used_faq_ids=[], needs_human=False, reasoning="t")
    app.dependency_overrides[get_llm_factory] = lambda: (lambda m: SlowFake())
    mid = (await signed(payload("What do you do?", "wamid.PAR"))).json()["new_message_ids"][0]
    rs = await asyncio.gather(*[client.post("/api/v1/agent/process", headers={"X-Internal-Api-Key": KEY},
                                            json={"message_id": mid}) for _ in range(5)])
    codes = sorted(r.status_code for r in rs)
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}
    outbound = await session.scalar(select(func.count()).select_from(Message)
                                    .where(Message.direction == MessageDirection.OUTBOUND))
    assert outbound == 1                            # exactly one AI reply, whatever the timing


async def test_urdu_and_emoji_round_trip(signed, client, session, make_agent):
    text = "السلام علیکم! ویب سائٹ کی قیمت کیا ہے؟ 🙏🏽 Roman: website kitne ki hai?"
    await signed(payload(text, "wamid.URDU"))
    stored = await session.scalar(select(Message.body))
    assert stored == text
    _, h = await make_agent(AgentRole.VIEWER)
    conv = (await client.get("/api/v1/conversations", headers=h)).json()["items"][0]
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}", headers=h)).json()
    assert detail["messages"][0]["body"] == text


async def test_oversized_payload_rejected(signed):
    r = await signed(b"{" + b" " * (3 * 1024 * 1024 + 10) + b"}")
    assert r.status_code == 413


async def test_unsigned_payload_never_reaches_database(client, signed, session):
    r = await client.post("/api/v1/webhooks/whatsapp", json=payload("hack", "wamid.HACK"))
    assert r.status_code == 401
    assert await session.scalar(select(func.count()).select_from(Message)) == 0


@pytest.mark.parametrize("q", ["'; DROP TABLE leads; --", "%", "_", "\\", "' OR '1'='1", "<script>alert(1)</script>"])
async def test_hostile_search_strings_are_harmless(client, session, make_agent, q):
    _, h = await make_agent(AgentRole.VIEWER)
    for url in ("/api/v1/leads", "/api/v1/customers", "/api/v1/conversations", "/api/v1/faqs"):
        r = await client.get(url, params={"q": q}, headers=h)
        assert r.status_code == 200, (url, r.text)
    # Parameterised queries: the table still exists and still works.
    assert await session.scalar(select(func.count()).select_from(Lead)) == 0
    assert (await session.execute(text("SELECT to_regclass('public.leads')"))).scalar() == "leads"


async def test_alg_none_token_rejected(client, make_agent):
    agent, _ = await make_agent(AgentRole.ADMIN)
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': str(agent.id), 'type': 'access', 'exp': 9999999999})}."
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


async def test_reply_length_limit(client, session, make_agent, signed):
    await signed(payload("hello", "wamid.LEN"))
    agent, h = await make_agent(AgentRole.SALES_AGENT)
    conv = (await client.get("/api/v1/conversations", headers=h)).json()["items"][0]
    await client.post(f"/api/v1/conversations/{conv['id']}/takeover", headers=h, json={})
    too_long = await client.post(f"/api/v1/conversations/{conv['id']}/reply", headers=h, json={"text": "x" * 4097})
    blank = await client.post(f"/api/v1/conversations/{conv['id']}/reply", headers=h, json={"text": "   "})
    assert too_long.status_code == 422 and blank.status_code == 422


async def test_new_customers_work_with_generic_query_plans(session):
    """Regression (found by the end-to-end test, not by unit tests).

    PostgreSQL may switch a prepared statement to a 'generic plan' after 5 runs on a pooled
    connection. `ON CONFLICT (customer_id) WHERE status = $param` then cannot be matched to the
    partial unique index and fails. We force generic plans from the first run to reproduce it
    deterministically instead of depending on pool timing."""
    from conftest import TEST_DB_URL
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.messaging import process_webhook
    from app.services.whatsapp import WAWebhook

    engine = create_async_engine(
        TEST_DB_URL, connect_args={"server_settings": {"plan_cache_mode": "force_generic_plan"}})
    settings = get_settings().model_copy(update={"wa_phone_number_id": ""})
    try:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            for i in range(3):
                wa = f"92300555{i:04d}"
                await process_webhook(s, WAWebhook.model_validate(payload(f"hi {i}", f"wamid.GEN.{i}", wa)), settings)
    finally:
        await engine.dispose()
    assert await session.scalar(select(func.count()).select_from(Message)) == 3


async def test_order_follows_arrival_not_mixed_clocks(signed, client, session, make_agent):
    """Regression (found by the Docker end-to-end test).

    Customer messages carry Meta's WHOLE-SECOND timestamp; our replies use the server clock.
    Reply saved at 12:00:00.400 between two customer messages both stamped 12:00:00 must still
    appear between them (insertion order), not after the second one."""
    from datetime import UTC, datetime

    from app.models import Conversation
    from app.models.enums import SendStatus, SenderType

    first = payload("first", "wamid.ORD.1")
    first["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "1763294400"   # 12:00:00
    await signed(first)
    conv = await session.scalar(select(Conversation))
    session.add(Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND, sender_type=SenderType.AI,
                        body="reply", send_status=SendStatus.SENT,
                        created_at=datetime(2026, 11, 16, 12, 0, 0, 400000, tzinfo=UTC)))
    await session.commit()
    second = payload("second", "wamid.ORD.2")
    second["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "1763294400"  # same second
    await signed(second)

    _, h = await make_agent(AgentRole.VIEWER)
    detail = (await client.get(f"/api/v1/conversations/{conv.id}", headers=h)).json()
    assert [m["body"] for m in detail["messages"]] == ["first", "reply", "second"]


async def test_database_down_gives_clean_error(client, monkeypatch):
    """Health reports 'degraded' (503) instead of crashing when the DB is unreachable."""
    from app.db import get_session

    class Broken:
        async def execute(self, *_):
            raise ConnectionError("db down")

    async def broken_session():
        yield Broken()
    app.dependency_overrides[get_session] = broken_session
    r = await client.get("/health")
    assert r.status_code == 503 and r.json()["database"] == "unreachable"
