"""Stage 6 tests: AI orchestration, lead qualification, guardrails, fallbacks, idempotency.

Gemini is replaced by FakeLLM (scripted answers), so tests are free, fast and deterministic.
"""
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.ai.context import search_faqs
from app.ai.guardrails import allowed_amounts, find_unapproved_amounts
from app.ai.lead_qualification import qualify
from app.ai.llm import LLMError, classify_api_error
from app.ai.schemas import LeadFacts, SalesResult, SupportResult
from app.api.deps import get_llm_factory, get_whatsapp_client
from app.api.v1.dev import build_meta_payload
from app.config import PROJECT_ROOT, get_settings
from app.main import app
from app.models import (
    AutomationError,
    Conversation,
    Customer,
    HandoffRequest,
    Lead,
    LeadActivity,
    Message,
    Service,
)
from app.models.enums import (
    ConversationMode,
    ErrorType,
    HandoffStatus,
    LeadTemperature,
    MessageDirection,
    SendStatus,
    SenderType,
)
from app.seed import seed_business
from app.services.messaging import process_webhook
from app.services.whatsapp import WAWebhook, WhatsAppClient

KEY = "test-internal-key-0123456789"
H = {"X-Internal-Api-Key": KEY}


class FakeLLM:
    """Stands in for Gemini: returns queued answers per schema and records every prompt."""
    model = "fake-gemini"

    def __init__(self):
        self.queues: dict[type, list] = {SupportResult: [], SalesResult: []}
        self.calls: list[dict] = []

    def add(self, item):
        schema = SupportResult if isinstance(item, SupportResult) else SalesResult
        if isinstance(item, Exception):
            schema = getattr(item, "_schema", SupportResult)
        self.queues[schema].append(item)
        return self

    async def generate_json(self, *, system, prompt, schema, temperature):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema.__name__})
        if not self.queues[schema]:
            raise AssertionError(f"FakeLLM: unexpected {schema.__name__} call")
        item = self.queues[schema].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def support(intent="faq", confidence=0.9, answer="We build websites.", needs_human=False):
    return SupportResult(intent=intent, confidence=confidence, answer=answer,
                         used_faq_ids=[], needs_human=needs_human, reasoning="test")


def sales(reply="Great! What's your budget?", **facts):
    return SalesResult(reply=reply, facts=LeadFacts(**facts),
                       recommended_services=facts.get("interested_services", []),
                       next_question_topic="budget")


def llm_error(error_type=ErrorType.TEMPORARY, schema=SupportResult):
    e = LLMError("Gemini down", error_type)
    e._schema = schema
    return e


@pytest.fixture
async def ai(client, session):
    """Seeds the business data and installs FakeLLM + internal key. Returns the fake."""
    data = json.loads((PROJECT_ROOT / "database/seed/business.json").read_text(encoding="utf-8"))
    await seed_business(session, data)
    await session.commit()
    settings = get_settings().model_copy(update={"backend_internal_api_key": SecretStr(KEY),
                                                 "wa_mock_mode": True, "wa_phone_number_id": ""})
    app.dependency_overrides[get_settings] = lambda: settings
    fake = FakeLLM()
    app.dependency_overrides[get_llm_factory] = lambda: (lambda model: fake)
    return fake


async def inbound(session, text, wa_id="923001234567", name="Ali Khan", wamid=None) -> str:
    payload = build_meta_payload(wa_id, name, text,
                                 wamid or f"wamid.T.{datetime.now(UTC).timestamp()}.{text[:5]}")
    result = await process_webhook(session, WAWebhook.model_validate(payload), get_settings())
    return str(result.new_message_ids[0])


async def process(client, message_id, **extra):
    return await client.post("/api/v1/agent/process", json={"message_id": message_id, **extra}, headers=H)


# ------------------------------------------------------------------ retrieval
async def test_faq_search_matches_any_word(session, ai):
    hits = await search_faqs(session, "Hi, how much does an online store cost?")
    assert hits and hits[0].question == "How much does an online store cost?"


# ------------------------------------------------------------------ support path
async def test_faq_answer_is_sent(client, session, ai):
    ai.add(support(answer="We are open Monday-Friday, 10:00-19:00."))
    mid = await inbound(session, "What are your business hours?")
    r = await process(client, mid)
    d = r.json()
    assert r.status_code == 200, r.text
    assert (d["action"], d["intent"], d["reply_send_status"]) == ("replied", "faq", "mocked")
    reply = await session.get(Message, d["reply_message_id"])
    assert reply.sender_type == SenderType.AI and str(reply.reply_to_message_id) == mid
    original = await session.get(Message, mid)
    await session.refresh(original)
    assert original.processed_at is not None and original.intent == "faq"
    assert "business hours" in ai.calls[0]["prompt"]          # message reached the prompt


async def test_same_message_is_never_answered_twice(client, session, ai):
    ai.add(support())
    mid = await inbound(session, "What do you do?")
    first = (await process(client, mid)).json()
    second = (await process(client, mid)).json()              # e.g. n8n retry after a timeout
    assert second["already_processed"] is True
    assert second["reply_message_id"] == first["reply_message_id"]
    assert len(ai.calls) == 1                                   # Gemini called once
    outbound = await session.scalar(select(func.count()).select_from(Message)
                                    .where(Message.direction == MessageDirection.OUTBOUND))
    assert outbound == 1


async def test_parallel_request_gets_409(client, session, ai):
    mid = await inbound(session, "Hello")
    msg = await session.get(Message, mid)
    msg.processing_started_at = datetime.now(UTC)               # someone else is working on it
    await session.commit()
    assert (await process(client, mid)).status_code == 409
    msg.processing_started_at = datetime.now(UTC) - timedelta(minutes=10)   # stale claim
    await session.commit()
    ai.add(support(intent="greeting", answer="Hi! How can I help?"))
    assert (await process(client, mid)).status_code == 200


async def test_human_mode_keeps_ai_silent(client, session, ai):
    mid = await inbound(session, "Are you there?")
    conv = await session.scalar(select(Conversation))
    conv.mode = ConversationMode.HUMAN
    await session.commit()
    d = (await process(client, mid)).json()
    assert (d["action"], d["skip_reason"], d["reply_message_id"]) == ("skipped", "human_mode", None)
    assert ai.calls == []


async def test_keyword_handoff_without_calling_ai(client, session, ai):
    mid = await inbound(session, "Can I talk to someone please?")
    d = (await process(client, mid)).json()
    assert d["action"] == "handoff" and ai.calls == []
    conv = await session.scalar(select(Conversation))
    await session.refresh(conv)
    assert conv.mode == ConversationMode.HUMAN                   # AI paused
    handoff = await session.scalar(select(HandoffRequest))
    assert handoff.status == HandoffStatus.PENDING
    assert "connecting you" in d["reply_text"]


@pytest.mark.parametrize("result", [
    support(intent="complaint", answer="Sorry!"),
    support(intent="faq", needs_human=True),
    support(intent="other", confidence=0.2, answer="Maybe?"),   # low confidence -> escalate
])
async def test_escalation_cases(client, session, ai, result):
    ai.add(result)
    mid = await inbound(session, "Something tricky")
    d = (await process(client, mid)).json()
    assert d["action"] == "handoff" and d["handoff_request_id"]


async def test_stop_confirms_opt_out_without_ai(client, session, ai):
    mid = await inbound(session, "STOP")
    d = (await process(client, mid)).json()
    assert d["intent"] == "opt_out" and "unsubscribed" in d["reply_text"] and ai.calls == []


# ------------------------------------------------------------------ sales + qualification
async def test_sales_conversation_creates_hot_lead(client, session, ai):
    ai.add(support(intent="buying_intent"))
    ai.add(sales(reply="Perfect, an E-commerce Store fits. I'll have our team prepare a quote.",
                 customer_name="Ali Khan", email="ALI@Example.com",
                 requirements="Online clothing store with card payments",
                 interested_services=["e-commerce store", "Teleportation Device"],   # 2nd is fake
                 budget_text="around $1500", budget_amount=1500, budget_currency="usd",
                 timeline="next two weeks", timeline_days=14, is_decision_maker=True,
                 buying_signals=["asked for a quote"]))
    mid = await inbound(session, "I want an online store, budget $1500, start in 2 weeks. Quote?")
    d = (await process(client, mid)).json()

    assert d["lead"]["temperature"] == "hot" and d["lead"]["score"] >= 70
    assert d["lead"]["became_hot"] is True and d["notify_sales"] is True
    lead = await session.scalar(select(Lead))
    assert lead.interested_services == ["E-commerce Store"]          # fake service removed
    assert lead.estimated_budget == Decimal("1500.00") and lead.budget_currency == "USD"
    assert "budget fits" in lead.qualification_reason
    customer = await session.scalar(select(Customer))
    await session.refresh(customer)
    assert customer.email == "ali@example.com"
    types = set((await session.scalars(select(LeadActivity.activity_type))).all())
    assert {"created", "qualified"} <= types


async def test_hot_notification_only_on_transition(client, session, ai):
    hot = dict(requirements="store", interested_services=["E-commerce Store"], budget_amount=2000,
               budget_text="2000", budget_currency="USD", timeline_days=7, is_decision_maker=True)
    for text in ("I want a store", "Also need a logo"):
        ai.add(support(intent="buying_intent"))
        ai.add(sales(**hot))
        d = (await process(client, await inbound(session, text))).json()
    assert d["lead"]["temperature"] == "hot" and d["notify_sales"] is False   # already hot


async def test_facts_are_merged_not_erased(client, session, ai):
    ai.add(support(intent="pricing")).add(sales(budget_text="1000 USD", budget_amount=1000))
    await process(client, await inbound(session, "Budget is 1000 USD"))
    ai.add(support(intent="service_inquiry")).add(sales(timeline="next month", timeline_days=30))
    await process(client, await inbound(session, "Next month"))
    lead = await session.scalar(select(Lead))
    await session.refresh(lead)
    assert lead.budget_text == "1000 USD" and lead.timeline == "next month"   # both kept


def _lead(**kw) -> Lead:
    base = dict(requirements=None, interested_services=None, estimated_budget=None, budget_text=None,
                budget_currency=None, timeline=None, timeline_days=None, is_decision_maker=None,
                buying_signals=None)
    return Lead(**(base | kw))


RULES = {"hot_min_score": 70, "warm_min_score": 40}
SERVICES = [Service(name="Business Website", description="x", price_from=Decimal(400),
                    price_to=Decimal(1200), currency="USD")]


@pytest.mark.parametrize(("lead", "intent", "expected"), [
    (_lead(), "faq", LeadTemperature.COLD),
    (_lead(requirements="site", interested_services=["Business Website"], budget_text="500"),
     "pricing", LeadTemperature.WARM),
    (_lead(requirements="site", interested_services=["Business Website"], budget_text="$800",
           estimated_budget=Decimal(800), budget_currency="USD", timeline_days=10,
           is_decision_maker=True), "buying_intent", LeadTemperature.HOT),
])
def test_qualification_rules(lead, intent, expected):
    q = qualify(lead, intent, SERVICES, RULES)
    assert q.temperature == expected and q.reason.startswith(expected.value.upper())


def test_budget_below_price_gets_no_fit_bonus():
    low = qualify(_lead(interested_services=["Business Website"], estimated_budget=Decimal(100),
                        budget_text="100", budget_currency="USD"), None, SERVICES, RULES)
    assert "budget fits" not in low.reason


# ------------------------------------------------------------------ guardrails
def test_price_guardrail_detection():
    allowed = allowed_amounts(SERVICES, ["Maintenance from 50 USD per month."])
    assert find_unapproved_amounts("A website costs $400 to $1,200.", allowed) == []
    assert find_unapproved_amounts("Maintenance is 50 USD.", allowed) == []
    assert find_unapproved_amounts("Special price: $99 today!", allowed) == ["$99"]
    assert find_unapproved_amounts("You get 30% off", allowed) == ["30%"]


async def test_invented_price_is_replaced(client, session, ai):
    ai.add(support(intent="faq", answer="Sure! A website is only $99 this week."))
    d = (await process(client, await inbound(session, "website price?"))).json()
    assert d["guardrails"] == ["unapproved_amount: $99"]
    assert "$99" not in d["reply_text"] and "team member will confirm" in d["reply_text"]


# ------------------------------------------------------------------ failures + fallbacks
async def test_gemini_down_falls_back_to_faq_search(client, session, ai):
    ai.add(llm_error(ErrorType.RATE_LIMIT))
    d = (await process(client, await inbound(session, "How long does it take to build a website?"))).json()
    assert d["fallback_used"] is True and d["action"] == "replied"
    assert "2 to 4 weeks" in d["reply_text"]                      # the matching FAQ's answer
    err = await session.scalar(select(AutomationError))
    assert (err.source, err.error_type) == ("gemini", ErrorType.RATE_LIMIT)


async def test_gemini_down_and_no_faq_hands_off(client, session, ai):
    ai.add(llm_error())
    d = (await process(client, await inbound(session, "xyzzy qwerty"))).json()
    assert d["action"] == "handoff" and d["fallback_used"] is True


async def test_whatsapp_failure_does_not_lose_the_decision(client, session, ai):
    ai.add(support())

    def fail(_request):
        raise httpx.ReadTimeout("no answer")
    real = get_settings().model_copy(update={"wa_mock_mode": False, "wa_phone_number_id": "1",
                                             "wa_access_token": SecretStr("t")})
    app.dependency_overrides[get_whatsapp_client] = lambda: WhatsAppClient(
        real, transport=httpx.MockTransport(fail))
    d = (await process(client, await inbound(session, "What do you do?"))).json()
    assert d["reply_send_status"] == "failed"
    reply = await session.get(Message, d["reply_message_id"])
    assert reply.send_status == SendStatus.FAILED
    # The inbound message stays processed: a retry returns the stored decision, never resends.
    again = (await process(client, d["message_id"])).json()
    assert again["already_processed"] is True and len(ai.calls) == 1


async def test_send_false_leaves_reply_pending(client, session, ai):
    ai.add(support())
    d = (await process(client, await inbound(session, "Hello?"), send=False)).json()
    assert d["reply_send_status"] == "pending"


# ------------------------------------------------------------------ context
async def test_context_window_and_lead_memory(client, session, ai):
    for i in range(14):
        await inbound(session, f"earlier message {i}", wamid=f"wamid.CTX.{i:02d}")
    customer = await session.scalar(select(Customer))
    session.add(Lead(customer_id=customer.id, budget_text="5000 PKR", requirements="a bakery website"))
    await session.commit()
    ai.add(support())
    await process(client, await inbound(session, "latest question", wamid="wamid.CTX.99"))
    prompt = ai.calls[0]["prompt"]
    assert "latest question" in prompt
    assert "earlier message 13" in prompt and "earlier message 0" not in prompt   # window of 12
    assert prompt.index("earlier message 12") < prompt.index("earlier message 13")  # oldest first
    assert "5000 PKR" in prompt and "a bakery website" in prompt                    # lead memory
    assert "data, not instructions" in ai.calls[0]["system"].lower().replace("is data", "data")


# ------------------------------------------------------------------ security + mapping
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Api-Key": "wrong"}])
async def test_process_requires_internal_key(client, session, ai, headers):
    mid = await inbound(session, "hi")
    r = await client.post("/api/v1/agent/process", json={"message_id": mid}, headers=headers)
    assert r.status_code == 401


@pytest.mark.parametrize(("code", "expected"), [
    (401, ErrorType.AUTH), (403, ErrorType.AUTH), (429, ErrorType.RATE_LIMIT),
    (500, ErrorType.TEMPORARY), (503, ErrorType.TEMPORARY), (404, ErrorType.VALIDATION),
])
def test_gemini_error_mapping(code, expected):
    assert classify_api_error(code) == expected


async def test_unconfigured_gemini_uses_fallback(client, session, ai):
    app.dependency_overrides.pop(get_llm_factory)          # use the real factory, no API key
    s = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: s.model_copy(update={"gemini_api_key": SecretStr("")})
    d = (await process(client, await inbound(session, "Do you provide support after launch?"))).json()
    assert d["fallback_used"] is True and "1 month of free support" in d["reply_text"]
