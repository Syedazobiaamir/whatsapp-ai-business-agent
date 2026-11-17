"""Stage 10: tests for code paths the coverage report showed as untested."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import SecretStr
from sqlalchemy import select

import app.services.recovery as recovery
from app.ai.llm import GeminiLLM, LLMError, build_llm, UnconfiguredLLM
from app.ai.schemas import SupportResult
from app.config import get_settings
from app.main import app
from app.models import AutomationError, Conversation, Customer, FollowUpTask, Lead, Message
from app.models.enums import (
    AgentRole,
    ConversationMode,
    ErrorStatus,
    ErrorType,
    FollowUpStatus,
    LeadStatus,
    LeadTemperature,
    MessageDirection,
    MessageType,
    RiskLevel,
    SendStatus,
    SenderType,
)
from app.services.followups import send_follow_up
from app.services.recovery import record_error, run_recovery
from app.services.whatsapp import WAWebhook, WhatsAppClient, extract_events

GOOD = json.dumps({"intent": "faq", "confidence": 0.9, "answer": "Hi", "used_faq_ids": [],
                   "needs_human": False, "reasoning": "ok"})


# ------------------------------------------------------------------ the REAL Gemini client code
def gemini_with(responses):
    """GeminiLLM with Google's SDK call replaced by a fake that returns/raises `responses` in order."""
    s = get_settings().model_copy(update={"gemini_api_key": SecretStr("test-key"), "gemini_model": "gemini-x"})
    llm = GeminiLLM(s)
    calls = []

    async def fake_generate(*, model, contents, config):
        calls.append({"model": model, "config": config})
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(text=r)
    llm._client.aio.models.generate_content = fake_generate      # no network
    return llm, calls


async def test_gemini_returns_validated_object_and_sends_schema():
    llm, calls = gemini_with([GOOD])
    out = await llm.generate_json(system="sys", prompt="p", schema=SupportResult, temperature=0.2)
    assert out.intent == "faq" and out.confidence == 0.9
    cfg = calls[0]["config"]
    assert calls[0]["model"] == "gemini-x"
    assert cfg.response_mime_type == "application/json" and cfg.temperature == 0.2
    assert cfg.response_json_schema["properties"]["intent"]["enum"][0] == "greeting"   # schema sent


async def test_gemini_invalid_json_retried_once_then_succeeds():
    llm, calls = gemini_with(["not json", GOOD])
    assert (await llm.generate_json(system="s", prompt="p", schema=SupportResult, temperature=0)).answer == "Hi"
    assert len(calls) == 2


async def test_gemini_bad_output_twice_raises_validation():
    bad = json.dumps({"intent": "dance", "confidence": 7})          # wrong enum + out of range
    llm, calls = gemini_with([bad, bad])
    with pytest.raises(LLMError) as exc:
        await llm.generate_json(system="s", prompt="p", schema=SupportResult, temperature=0)
    assert exc.value.error_type == ErrorType.VALIDATION and len(calls) == 2


@pytest.mark.parametrize(("code", "expected"), [(429, ErrorType.RATE_LIMIT), (401, ErrorType.AUTH),
                                                (503, ErrorType.TEMPORARY)])
async def test_gemini_api_errors_are_mapped(code, expected):
    err = genai_errors.APIError(code, {"error": {"code": code, "message": "boom", "status": "X"}})
    llm, _ = gemini_with([err])
    with pytest.raises(LLMError) as exc:
        await llm.generate_json(system="s", prompt="p", schema=SupportResult, temperature=0)
    assert exc.value.error_type == expected


async def test_gemini_network_error_is_temporary():
    llm, _ = gemini_with([httpx.ConnectError("offline")])
    with pytest.raises(LLMError) as exc:
        await llm.generate_json(system="s", prompt="p", schema=SupportResult, temperature=0)
    assert exc.value.error_type == ErrorType.TEMPORARY


def test_build_llm_without_key_is_unconfigured():
    s = get_settings().model_copy(update={"gemini_api_key": SecretStr("")})
    assert isinstance(build_llm(s), UnconfiguredLLM)


# ------------------------------------------------------------------ WhatsApp payload types
def test_button_and_list_replies_are_read_as_text():
    def msg(**kw):
        return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages",
                "value": {"messaging_product": "whatsapp", "metadata": {}, "contacts": [],
                          "messages": [{"from": "92300", "id": f"wamid.{kw['type']}", "timestamp": "1", **kw}]}}]}]}
    cases = [
        (msg(type="button", button={"text": "Yes, call me"}), "Yes, call me", MessageType.INTERACTIVE),
        (msg(type="interactive", interactive={"type": "list_reply", "list_reply": {"title": "Website"}}),
         "Website", MessageType.INTERACTIVE),
        (msg(type="document", document={"caption": "brief.pdf"}), "brief.pdf", MessageType.DOCUMENT),
        (msg(type="location", location={"latitude": 1}), "[location message]", MessageType.OTHER),
    ]
    for payload, text, mtype in cases:
        (m,), _ = extract_events(WAWebhook.model_validate(payload))
        assert (m.body, m.message_type) == (text, mtype)


# ------------------------------------------------------------------ follow-up send-time re-checks
async def approved_task(session, *, status=LeadStatus.NEW, mode=ConversationMode.AI, replied=False, text=False):
    now = datetime.now(UTC)
    c = Customer(wa_id="923001112233", name="Zara", marketing_opt_in=True)
    session.add(c)
    await session.flush()
    conv = Conversation(customer_id=c.id, mode=mode,
                        last_customer_message_at=now - (timedelta(minutes=1) if replied else timedelta(hours=30)))
    session.add(conv)
    await session.flush()
    lead = Lead(customer_id=c.id, status=status, temperature=LeadTemperature.HOT)
    session.add(lead)
    await session.flush()
    task = FollowUpTask(lead_id=lead.id, customer_id=c.id, scheduled_at=now, status=FollowUpStatus.APPROVED,
                        message_type=MessageType.TEXT if text else MessageType.TEMPLATE,
                        template_name=None if text else "hello_world", message_body="Hi" if text else None,
                        idempotency_key=f"k-{status}-{mode}-{replied}-{text}")
    session.add(task)
    await session.commit()
    if replied:                                   # make the reply come AFTER planning
        task.created_at = now - timedelta(hours=2)
        await session.commit()
    return task


@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"status": LeadStatus.WON}, "lead is won"),
    ({"mode": ConversationMode.HUMAN}, "a human is handling"),
    ({"replied": True}, "customer replied"),
    ({"text": True}, "template is required"),
])
async def test_follow_up_rechecks_at_send_time(session, kwargs, reason):
    task = await approved_task(session, **kwargs)
    wa = WhatsAppClient(get_settings().model_copy(update={"wa_mock_mode": True}))
    out = await send_follow_up(session, wa, task.id)
    assert out.status == "skipped" and reason in out.detail


# ------------------------------------------------------------------ recovery branches
async def test_follow_up_error_tracks_task_outcome(session):
    task = await approved_task(session)
    row, _ = await record_error(session, source="whatsapp", operation="follow_up", error_type=ErrorType.RATE_LIMIT,
                                risk=RiskLevel.SAFE, message="rate", retryable=True,
                                payload={"follow_up_task_id": str(task.id)}, unique_key=str(task.id))
    row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    wa = WhatsAppClient(get_settings())
    rep = await run_recovery(session, wa, get_settings())
    assert rep.rescheduled == [str(row.id)]                     # still approved: WA 4 retries
    task.status = FollowUpStatus.SENT
    row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    rep = await run_recovery(session, wa, get_settings())
    assert rep.resolved == [str(row.id)]


async def test_n8n_down_during_retry_escalates(session, monkeypatch):
    def down(_):
        raise httpx.ConnectError("n8n offline")
    monkeypatch.setattr(recovery, "n8n_transport", lambda: httpx.MockTransport(down))
    s = get_settings().model_copy(update={"n8n_api_key": SecretStr("k")})
    row, _ = await record_error(session, source="n8n", operation="n8n_execution", error_type=ErrorType.TEMPORARY,
                                risk=RiskLevel.SAFE, message="ECONNREFUSED", retryable=True, execution_id="5",
                                workflow_id="WaAgentIncoming1")
    row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    rep = await run_recovery(session, WhatsAppClient(s), s)
    await session.refresh(row)
    assert rep.escalated == [str(row.id)] and row.status == ErrorStatus.OPEN
    assert "Automatic retry impossible" in row.resolution_note


async def test_retry_gives_up_after_max_when_repeats_keep_coming(session):
    kw = dict(source="n8n", operation="n8n_execution", error_type=ErrorType.TEMPORARY, risk=RiskLevel.SAFE,
              message="timeout", retryable=True, workflow_id="WaAgentIncoming1")
    row, _ = await record_error(session, **kw)
    row.retry_count = row.max_retries
    row.alerted_at = datetime.now(UTC)
    await session.commit()
    again, is_new = await record_error(session, **kw)                 # one more failure after max retries
    assert again.id == row.id and not is_new
    assert again.status == ErrorStatus.OPEN and again.alerted_at is None   # re-alerted


# ------------------------------------------------------------------ team + customers API
async def test_team_update_and_customer_search(client, session, make_agent):
    admin, h = await make_agent(AgentRole.ADMIN)
    sales, _ = await make_agent(AgentRole.SALES_AGENT)
    r = await client.patch(f"/api/v1/team/{sales.id}", headers=h,
                           json={"is_active": False, "password": "New-Password-123", "role": None})
    assert r.status_code == 200 and r.json()["is_active"] is False and r.json()["role"] == "sales_agent"
    assert (await client.patch("/api/v1/team/00000000-0000-0000-0000-000000000000", headers=h,
                               json={"phone": "1"})).status_code == 404

    session.add_all([Customer(wa_id="923000000111", name="Ayesha", company="Bakery Co"),
                     Customer(wa_id="923000000222", name="Bilal")])
    await session.commit()
    found = (await client.get("/api/v1/customers", params={"q": "bakery"}, headers=h)).json()
    assert [c["name"] for c in found["items"]] == ["Ayesha"]
