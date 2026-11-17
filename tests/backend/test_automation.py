"""Stage 7 tests: the backend endpoints that n8n workflows call (all must be idempotent)."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.api.deps import get_whatsapp_client
from app.config import PROJECT_ROOT, get_settings
from app.core.security import hash_password
from app.main import app
from app.models import (
    AuditLog,
    AutomationError,
    Conversation,
    Customer,
    FollowUpTask,
    HandoffRequest,
    HumanAgent,
    Lead,
    LeadActivity,
    Message,
)
from app.models.enums import (
    ActorType,
    AgentRole,
    ConversationMode,
    FollowUpStatus,
    HandoffStatus,
    LeadStatus,
    LeadTemperature,
    MessageDirection,
    SendStatus,
    SenderType,
)
from app.seed import seed_business
from app.services.whatsapp import WhatsAppClient

KEY = "test-internal-key-0123456789"
H = {"X-Internal-Api-Key": KEY}
NOW = datetime.now(UTC)


@pytest.fixture
async def env(client, session):
    data = json.loads((PROJECT_ROOT / "database/seed/business.json").read_text(encoding="utf-8"))
    await seed_business(session, data)
    await session.commit()
    s = get_settings().model_copy(update={"backend_internal_api_key": SecretStr(KEY), "wa_mock_mode": True,
                                          "sales_notify_email": "sales@test.dev",
                                          "admin_alert_email": "boss@test.dev"})
    app.dependency_overrides[get_settings] = lambda: s
    return s


async def make_lead(session, *, temp=LeadTemperature.HOT, opt_in=True, last_dir=MessageDirection.OUTBOUND,
                    hours_ago=30, mode=ConversationMode.AI, status=LeadStatus.NEW, wa_id="923001234567"):
    c = Customer(wa_id=wa_id, name="Ali", marketing_opt_in=opt_in)
    session.add(c)
    await session.flush()
    then = NOW - timedelta(hours=hours_ago)
    conv = Conversation(customer_id=c.id, mode=mode, last_message_at=then,
                        last_customer_message_at=then - timedelta(minutes=5))
    session.add(conv)
    await session.flush()
    session.add(Message(conversation_id=conv.id, direction=MessageDirection.INBOUND,
                        sender_type=SenderType.CUSTOMER, body="I want a store",
                        created_at=then - timedelta(minutes=5), wa_message_id=f"wamid.{wa_id}"))
    if last_dir == MessageDirection.OUTBOUND:
        session.add(Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND,
                            sender_type=SenderType.AI, body="What's your budget?", created_at=then,
                            send_status=SendStatus.SENT))
    lead = Lead(customer_id=c.id, conversation_id=conv.id, temperature=temp, score=80, status=status,
                requirements="online store", qualification_reason="HOT (80/100): test")
    session.add(lead)
    await session.commit()
    return c, conv, lead


# ------------------------------------------------------------------ security
@pytest.mark.parametrize("url", ["/api/v1/internal/follow-ups/plan", "/api/v1/internal/handoffs/auto-resume"])
async def test_internal_routes_need_key(client, env, url):
    assert (await client.post(url)).status_code == 401
    assert (await client.post(url, headers={"X-Internal-Api-Key": "nope"})).status_code == 401


# ------------------------------------------------------------------ send pending reply
async def test_pending_message_is_sent_exactly_once(client, session, env):
    _, conv, _ = await make_lead(session, hours_ago=1)
    msg = Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND, sender_type=SenderType.AI,
                  body="Hello!", send_status=SendStatus.PENDING)
    session.add(msg)
    await session.commit()
    first = (await client.post(f"/api/v1/internal/messages/{msg.id}/send", headers=H)).json()
    second = (await client.post(f"/api/v1/internal/messages/{msg.id}/send", headers=H)).json()
    assert first["send_status"] == "mocked"
    # Mock mode generates a NEW random id per send, so an equal id proves nothing was re-sent.
    assert second["wa_message_id"] == first["wa_message_id"]


# ------------------------------------------------------------------ Workflow 2: hot lead email
async def test_lead_notification_is_deduplicated(client, session, env):
    _, _, lead = await make_lead(session)
    n = (await client.get(f"/api/v1/internal/leads/{lead.id}/notification", headers=H)).json()
    assert n["already_notified"] is False and n["to"] == "sales@test.dev"
    assert "Hot lead" in n["subject"] and f"/leads/{lead.id}" in n["body"]
    await client.post(f"/api/v1/internal/leads/{lead.id}/notified", headers=H,
                      json={"channel": "log", "to": "sales@test.dev"})
    again = (await client.get(f"/api/v1/internal/leads/{lead.id}/notification", headers=H)).json()
    assert again["already_notified"] is True


# ------------------------------------------------------------------ Workflow 3: handoff
async def _agent(session, name, role=AgentRole.SALES_AGENT):
    a = HumanAgent(full_name=name, email=f"{name.lower()}@test.dev", role=role,
                   password_hash=hash_password("Whatever-123"))
    session.add(a)
    await session.commit()
    return a


async def test_handoff_assigned_to_least_busy_agent(client, session, env):
    sara = await _agent(session, "Sara")
    omar = await _agent(session, "Omar")
    _, conv1, _ = await make_lead(session, wa_id="923000000001")
    _, conv2, _ = await make_lead(session, wa_id="923000000002")
    session.add(HandoffRequest(conversation_id=conv1.id, requested_by=ActorType.AI, reason="x",
                               assigned_agent_id=sara.id))
    h2 = HandoffRequest(conversation_id=conv2.id, requested_by=ActorType.CUSTOMER, reason="wants human")
    session.add(h2)
    await session.commit()

    r = (await client.post(f"/api/v1/internal/handoffs/{h2.id}/assign", headers=H)).json()
    assert r["agent"]["name"] == "Omar" and r["to"] == "omar@test.dev"     # Sara already busy
    again = (await client.post(f"/api/v1/internal/handoffs/{h2.id}/assign", headers=H)).json()
    assert again["agent"]["id"] == str(omar.id)                            # idempotent
    await session.refresh(conv2)
    assert conv2.assigned_agent_id == omar.id
    assert "AI is paused" in r["body"]


async def test_handoff_without_agents_goes_to_admin_email(client, session, env):
    _, conv, _ = await make_lead(session)
    h = HandoffRequest(conversation_id=conv.id, requested_by=ActorType.AI, reason="complaint")
    session.add(h)
    await session.commit()
    r = (await client.post(f"/api/v1/internal/handoffs/{h.id}/assign", headers=H)).json()
    assert r["agent"] is None and r["to"] == "boss@test.dev"


async def test_auto_resume_only_when_safe(client, session, env):
    sara = await _agent(session, "Sara")
    _, idle, _ = await make_lead(session, mode=ConversationMode.HUMAN, hours_ago=30, wa_id="923000000001")
    _, busy, _ = await make_lead(session, mode=ConversationMode.HUMAN, hours_ago=30, wa_id="923000000002")
    _, fresh, _ = await make_lead(session, mode=ConversationMode.HUMAN, hours_ago=2, wa_id="923000000003")
    session.add(HandoffRequest(conversation_id=idle.id, requested_by=ActorType.AI, reason="x",
                               status=HandoffStatus.ACCEPTED))
    session.add(Message(conversation_id=busy.id, direction=MessageDirection.OUTBOUND,
                        sender_type=SenderType.HUMAN, sent_by_agent_id=sara.id, body="On it!",
                        created_at=NOW - timedelta(hours=3), send_status=SendStatus.SENT))
    await session.commit()

    r = (await client.post("/api/v1/internal/handoffs/auto-resume", headers=H)).json()
    assert r["resumed"] == [str(idle.id)]               # busy (human replied) + fresh stay human
    h = await session.scalar(select(HandoffRequest).where(HandoffRequest.conversation_id == idle.id))
    await session.refresh(h)
    assert h.status == HandoffStatus.RESOLVED and "auto-resumed" in h.resolution_note


# ------------------------------------------------------------------ Workflow 4: follow-ups
@pytest.mark.parametrize(("kwargs", "expected"), [
    ({}, 1),                                                    # eligible
    ({"opt_in": False}, 0),                                     # no marketing consent
    ({"last_dir": MessageDirection.INBOUND}, 0),                # customer spoke last
    ({"mode": ConversationMode.HUMAN}, 0),                      # human handling
    ({"status": LeadStatus.WON}, 0),                            # already won
    ({"temp": LeadTemperature.COLD}, 0),                        # cold leads are not chased
    ({"hours_ago": 5}, 0),                                      # too early
])
async def test_follow_up_eligibility(client, session, env, kwargs, expected):
    await make_lead(session, **kwargs)
    r = (await client.post("/api/v1/internal/follow-ups/plan", headers=H)).json()
    assert r["created"] == expected


async def test_follow_up_planning_is_idempotent_and_uses_template(client, session, env):
    await make_lead(session)
    assert (await client.post("/api/v1/internal/follow-ups/plan", headers=H)).json()["created"] == 1
    assert (await client.post("/api/v1/internal/follow-ups/plan", headers=H)).json()["created"] == 0
    task = await session.scalar(select(FollowUpTask))
    assert task.template_name == "hello_world"                  # >24h since customer: template
    assert task.status == FollowUpStatus.PENDING_APPROVAL       # human approval required


async def test_approve_then_send_once(client, session, env, make_agent):
    _, conv, lead = await make_lead(session)
    await client.post("/api/v1/internal/follow-ups/plan", headers=H)
    task = await session.scalar(select(FollowUpTask))
    assert (await client.get("/api/v1/internal/follow-ups/due", headers=H)).json()["task_ids"] == []

    _, viewer = await make_agent(AgentRole.VIEWER)
    assert (await client.post(f"/api/v1/follow-ups/{task.id}/approve", headers=viewer)).status_code == 403
    _, staff = await make_agent(AgentRole.SALES_AGENT)
    assert (await client.post(f"/api/v1/follow-ups/{task.id}/approve", headers=staff)).status_code == 200
    assert (await client.post(f"/api/v1/follow-ups/{task.id}/approve", headers=staff)).status_code == 409

    due = (await client.get("/api/v1/internal/follow-ups/due", headers=H)).json()["task_ids"]
    assert due == [str(task.id)]
    sent = (await client.post(f"/api/v1/internal/follow-ups/{task.id}/send", headers=H)).json()
    assert sent["status"] == "sent"
    again = (await client.post(f"/api/v1/internal/follow-ups/{task.id}/send", headers=H)).json()
    assert again["status"] == "sent" and again["detail"] == "not approved / already handled"
    count = await session.scalar(select(func.count()).select_from(Message).where(
        Message.sender_type == SenderType.SYSTEM))
    assert count == 1
    acts = set((await session.scalars(select(LeadActivity.activity_type)
                                      .where(LeadActivity.lead_id == lead.id))).all())
    assert {"follow_up_planned", "follow_up_sent"} <= acts


async def test_send_time_recheck_respects_opt_out(client, session, env):
    customer, _, _ = await make_lead(session)
    await client.post("/api/v1/internal/follow-ups/plan", headers=H)
    task = await session.scalar(select(FollowUpTask))
    task.status = FollowUpStatus.APPROVED
    customer.opted_out_at = NOW                                  # customer sent STOP meanwhile
    await session.commit()
    r = (await client.post(f"/api/v1/internal/follow-ups/{task.id}/send", headers=H)).json()
    assert r["status"] == "skipped" and "consent" in r["detail"]


class Meta:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def client(self):
        def handler(req):
            self.requests.append(req)
            r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
            if isinstance(r, Exception):
                raise r
            return r
        s = get_settings().model_copy(update={"wa_mock_mode": False, "wa_phone_number_id": "1",
                                              "wa_access_token": SecretStr("t"), "wa_max_send_attempts": 1})
        async def no_sleep(_):
            return None
        return WhatsAppClient(s, transport=httpx.MockTransport(handler), sleep=no_sleep)


async def _approved_task(client, session, rules_patch=None):
    if rules_patch:
        from app.models import AppSetting
        row = await session.get(AppSetting, "follow_up_rules")
        row.value = {**row.value, **rules_patch}
        await session.commit()
    await make_lead(session)
    await client.post("/api/v1/internal/follow-ups/plan", headers=H)
    task = await session.scalar(select(FollowUpTask))
    task.status = FollowUpStatus.APPROVED
    await session.commit()
    return task


@pytest.mark.parametrize(("uses_name", "has_components"), [(False, False), (True, True)])
async def test_template_parameters_only_when_configured(client, session, env, uses_name, has_components):
    meta = Meta(httpx.Response(200, json={"messages": [{"id": "wamid.X"}]}))
    app.dependency_overrides[get_whatsapp_client] = meta.client
    task = await _approved_task(client, session, {"template_uses_customer_name": uses_name})
    await client.post(f"/api/v1/internal/follow-ups/{task.id}/send", headers=H)
    template = json.loads(meta.requests[0].content)["template"]
    assert template["name"] == "hello_world"
    assert ("components" in template) is has_components


async def test_follow_up_failures(client, session, env):
    meta = Meta(httpx.Response(429, json={"error": {"code": 130429, "message": "rate"}}))
    app.dependency_overrides[get_whatsapp_client] = meta.client
    task = await _approved_task(client, session)
    r = (await client.post(f"/api/v1/internal/follow-ups/{task.id}/send", headers=H)).json()
    await session.refresh(task)
    assert r["status"] == "retry_later" and task.status == FollowUpStatus.APPROVED and task.attempts == 1

    meta.responses = [httpx.ReadTimeout("lost")]                # outcome unknown now
    r = (await client.post(f"/api/v1/internal/follow-ups/{task.id}/send", headers=H)).json()
    await session.refresh(task)
    assert r["status"] == "failed" and task.status == FollowUpStatus.FAILED
    risky = await session.scalar(select(AutomationError).where(AutomationError.status == "needs_approval"))
    assert risky is not None


async def test_notification_log_is_audited(client, session, env):
    r = await client.post("/api/v1/internal/notifications/log", headers=H, json={
        "channel": "log", "to": "sales@test.dev", "subject": "Hot lead", "body": "..."})
    assert r.status_code == 200
    assert await session.scalar(select(AuditLog).where(AuditLog.action == "notification.sent"))
