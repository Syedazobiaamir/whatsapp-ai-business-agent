"""Stage 9 tests: error classification, grouping, safe automatic retries, human approval, alerts."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

import app.services.recovery as recovery
from app.api.deps import get_whatsapp_client
from app.config import get_settings
from app.main import app
from app.models import AuditLog, AutomationError, Conversation, Customer, Message
from app.models.enums import (
    AgentRole,
    ErrorStatus,
    ErrorType,
    MessageDirection,
    RiskLevel,
    SendStatus,
    SenderType,
)
from app.services.recovery import classify_message, record_error
from app.services.whatsapp import WhatsAppClient

KEY = "test-internal-key-0123456789"
H = {"X-Internal-Api-Key": KEY}


@pytest.fixture
def settings(client):
    s = get_settings().model_copy(update={"backend_internal_api_key": SecretStr(KEY), "wa_mock_mode": True,
                                          "admin_alert_email": "ops@test.dev", "n8n_api_key": SecretStr("n8n-k"),
                                          "n8n_base_url": "http://n8n.test"})
    app.dependency_overrides[get_settings] = lambda: s
    return s


def fake_meta(*responses):
    calls = []

    def handler(req):
        calls.append(req)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    s = get_settings().model_copy(update={"wa_mock_mode": False, "wa_phone_number_id": "1",
                                          "wa_access_token": SecretStr("t"), "wa_max_send_attempts": 1})

    async def nosleep(_):
        return None
    app.dependency_overrides[get_whatsapp_client] = lambda: WhatsAppClient(
        s, transport=httpx.MockTransport(handler), sleep=nosleep)
    return calls


OK = httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]})
RATE = httpx.Response(429, json={"error": {"code": 130429, "message": "rate limit"}})


async def failed_message(session) -> Message:
    c = Customer(wa_id="923001234567", name="Ali")
    session.add(c)
    await session.flush()
    conv = Conversation(customer_id=c.id, last_customer_message_at=datetime.now(UTC))
    session.add(conv)
    await session.flush()
    msg = Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND, sender_type=SenderType.AI,
                  body="Hello!", send_status=SendStatus.PENDING)
    session.add(msg)
    await session.commit()
    return msg


async def send_via_api(client, msg):
    return (await client.post(f"/api/v1/internal/messages/{msg.id}/send", headers=H)).json()


# ------------------------------------------------------------------ classification
@pytest.mark.parametrize(("text", "code", "expected"), [
    ("connect ECONNREFUSED 127.0.0.1:8000", None, ErrorType.TEMPORARY),
    ("The service was not able to process your request (timeout)", None, ErrorType.TEMPORARY),
    ("Bad Gateway", 502, ErrorType.TEMPORARY),
    ("Too Many Requests", 429, ErrorType.RATE_LIMIT),
    ("Authorization failed - please check your credentials", 401, ErrorType.AUTH),
    ("Unprocessable entity", 422, ErrorType.VALIDATION),
    ("Something odd", None, ErrorType.UNKNOWN),
    # Exact messages n8n 2.11 produced in the live error drill / its friendly error texts:
    ("The service refused the connection - perhaps it is offline", None, ErrorType.TEMPORARY),
    ("The connection timed out, consider setting the 'Retry on Fail' option", None, ErrorType.TEMPORARY),
    ("The service is receiving too many requests from you", None, ErrorType.RATE_LIMIT),
    ("The resource you are requesting could not be found", None, ErrorType.VALIDATION),
])
def test_classification(text, code, expected):
    assert classify_message(text, code) == expected


# ------------------------------------------------------------------ grouping
async def test_repeats_are_grouped_into_one_row(session):
    for i in range(5):
        await record_error(session, source="gemini", operation="gemini", error_type=ErrorType.AUTH,
                           risk=RiskLevel.SAFE, message=f"GEMINI_API_KEY is not set (msg {i})", retryable=False)
    await session.commit()
    rows = (await session.scalars(select(AutomationError))).all()
    assert len(rows) == 1 and rows[0].occurrences == 5 and rows[0].status == ErrorStatus.OPEN


async def test_resolved_errors_start_a_new_group(session):
    row, _ = await record_error(session, source="gemini", operation="gemini", error_type=ErrorType.AUTH,
                                risk=RiskLevel.SAFE, message="key missing", retryable=False)
    recovery.resolve(row, "fixed")
    await session.commit()
    _, is_new = await record_error(session, source="gemini", operation="gemini", error_type=ErrorType.AUTH,
                                   risk=RiskLevel.SAFE, message="key missing", retryable=False)
    assert is_new is True


# ------------------------------------------------------------------ n8n reports (Error Trigger)
@pytest.mark.parametrize(("workflow_id", "msg", "expected_status", "risk", "alert"), [
    ("WaAgentIncoming1", "connect ECONNREFUSED", "retrying", "safe", False),   # idempotent wf: auto retry
    ("WaAgentLeadNoti1", "connect ECONNREFUSED", "needs_approval", "risky", True),  # email wf: approval
    ("WaAgentIncoming1", "401 Unauthorized", "open", "safe", True),           # needs a fix: alert
])
async def test_n8n_error_decisions(client, settings, workflow_id, msg, expected_status, risk, alert):
    r = (await client.post("/api/v1/internal/automation-errors", headers=H, json={
        "workflow_id": workflow_id, "workflow_name": "WF", "execution_id": "77", "node_name": "AI agent (backend)",
        "error_message": msg})).json()
    assert (r["status"], r["risk_level"], r["alert"]) == (expected_status, risk, alert)
    if alert:
        assert r["to"] == "ops@test.dev" and "/automation" in r["body"]


async def test_same_n8n_error_twice_alerts_once(client, settings):
    body = {"workflow_id": "WaAgentHandoff01", "execution_id": "1", "node_name": "Send Gmail",
            "error_message": "401 invalid credentials"}
    first = (await client.post("/api/v1/internal/automation-errors", headers=H, json=body)).json()
    await client.post(f"/api/v1/internal/automation-errors/{first['error_id']}/alerted", headers=H)
    second = (await client.post("/api/v1/internal/automation-errors", headers=H,
                                json=body | {"execution_id": "2"})).json()
    assert second["error_id"] == first["error_id"] and second["occurrences"] == 2
    assert second["alert"] is False                                  # already announced
    alerts = (await client.get("/api/v1/internal/recovery/alerts", headers=H)).json()["alerts"]
    assert alerts == []


# ------------------------------------------------------------------ safe automatic retries
async def test_rate_limited_message_is_retried_automatically(client, session, settings):
    msg = await failed_message(session)
    calls = fake_meta(RATE, OK)                     # 1st send rate-limited, retry succeeds
    assert (await send_via_api(client, msg))["send_status"] == "failed"
    err = await session.scalar(select(AutomationError))
    assert (err.status, err.risk_level) == (ErrorStatus.RETRYING, RiskLevel.SAFE)
    assert (await client.get("/api/v1/internal/recovery/alerts", headers=H)).json()["alerts"] == []  # no noise

    err.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)    # retry time has come
    await session.commit()
    report = (await client.post("/api/v1/internal/recovery/run", headers=H)).json()
    assert report["resolved"] == [str(err.id)]
    await session.refresh(msg)
    await session.refresh(err)
    assert msg.send_status == SendStatus.SENT and err.status == ErrorStatus.RESOLVED
    assert len(calls) == 2


async def test_retries_stop_at_max_then_alert(client, session, settings):
    msg = await failed_message(session)
    fake_meta(RATE)                                 # Meta keeps rate-limiting
    await send_via_api(client, msg)
    err = await session.scalar(select(AutomationError))
    for _ in range(err.max_retries):
        err.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        await client.post("/api/v1/internal/recovery/run", headers=H)
        await session.refresh(err)
    assert err.status == ErrorStatus.OPEN and err.retry_count == err.max_retries
    alerts = (await client.get("/api/v1/internal/recovery/alerts", headers=H)).json()["alerts"]
    assert [a["error_id"] for a in alerts] == [str(err.id)]


async def test_recovery_never_resends_a_delivered_message(client, session, settings):
    msg = await failed_message(session)
    fake_meta(RATE)
    await send_via_api(client, msg)
    err = await session.scalar(select(AutomationError))
    msg.send_status = SendStatus.DELIVERED          # e.g. a late status webhook proved delivery
    err.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    calls = fake_meta(OK)
    await client.post("/api/v1/internal/recovery/run", headers=H)
    await session.refresh(err)
    assert err.status == ErrorStatus.RESOLVED and "nothing to resend" in err.resolution_note
    assert calls == []


async def test_risky_errors_are_never_auto_retried(client, session, settings):
    msg = await failed_message(session)
    fake_meta(httpx.ReadTimeout("lost"))            # delivery unknown
    await send_via_api(client, msg)
    err = await session.scalar(select(AutomationError))
    assert (err.status, err.risk_level) == (ErrorStatus.NEEDS_APPROVAL, RiskLevel.RISKY)
    err.next_retry_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    calls = fake_meta(OK)
    report = (await client.post("/api/v1/internal/recovery/run", headers=H)).json()
    assert report == {"resolved": [], "rescheduled": [], "escalated": []} and calls == []


# ------------------------------------------------------------------ human approval
async def test_risky_retry_needs_admin_approval(client, session, settings, make_agent):
    msg = await failed_message(session)
    fake_meta(httpx.ReadTimeout("lost"))
    await send_via_api(client, msg)
    err = await session.scalar(select(AutomationError))

    _, sales = await make_agent(AgentRole.SALES_AGENT)
    r = await client.post(f"/api/v1/automation-errors/{err.id}/approve-retry", headers=sales)
    assert r.status_code == 403                                     # sales cannot approve risky

    calls = fake_meta(OK)
    admin, h = await make_agent(AgentRole.ADMIN)
    r = await client.post(f"/api/v1/automation-errors/{err.id}/approve-retry", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    await session.refresh(msg)
    assert msg.send_status == SendStatus.SENT and len(calls) == 1   # sent exactly once more
    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "automation_error.retry_approved"))
    assert audit.actor_agent_id == admin.id and audit.changes["risk_level"] == "risky"
    again = await client.post(f"/api/v1/automation-errors/{err.id}/approve-retry", headers=h)
    assert again.status_code == 409


async def test_resolve_and_ignore(client, session, make_agent):
    rows = []
    for m in ("a", "b"):
        row, _ = await record_error(session, source="gemini", operation="gemini", error_type=ErrorType.AUTH,
                                    risk=RiskLevel.SAFE, message=m, retryable=False)
        rows.append(row)
    await session.commit()
    _, h = await make_agent(AgentRole.SALES_AGENT)
    r = await client.post(f"/api/v1/automation-errors/{rows[0].id}/resolve", headers=h, json={"note": "Key added"})
    assert r.json()["status"] == "resolved" and r.json()["resolution_note"] == "Key added"
    r = await client.post(f"/api/v1/automation-errors/{rows[1].id}/ignore", headers=h, json={})
    assert r.json()["status"] == "ignored"
    _, viewer = await make_agent(AgentRole.VIEWER)
    assert (await client.post(f"/api/v1/automation-errors/{rows[0].id}/resolve", headers=viewer,
                              json={})).status_code == 403


async def test_not_retryable_operation_rejected(client, session, make_agent):
    row, _ = await record_error(session, source="gemini", operation="gemini", error_type=ErrorType.AUTH,
                                risk=RiskLevel.SAFE, message="key", retryable=False)
    await session.commit()
    _, h = await make_agent(AgentRole.ADMIN)
    assert (await client.post(f"/api/v1/automation-errors/{row.id}/approve-retry", headers=h)).status_code == 422


# ------------------------------------------------------------------ n8n execution retry
async def test_n8n_execution_retry_and_verification(client, session, settings, monkeypatch):
    seen = []

    def fake_n8n(req):
        seen.append(req)
        return httpx.Response(200, json={"id": "78"})
    monkeypatch.setattr(recovery, "n8n_transport", lambda: httpx.MockTransport(fake_n8n))

    r = (await client.post("/api/v1/internal/automation-errors", headers=H, json={
        "workflow_id": "WaAgentIncoming1", "execution_id": "77", "node_name": "AI agent (backend)",
        "error_message": "connect ECONNREFUSED 127.0.0.1:8000"})).json()
    err = await session.get(AutomationError, r["error_id"])
    err.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    rep = (await client.post("/api/v1/internal/recovery/run", headers=H)).json()
    assert rep["rescheduled"] == [str(err.id)]
    assert str(seen[0].url) == "http://n8n.test/api/v1/executions/77/retry"
    assert seen[0].headers["X-N8N-API-KEY"] == "n8n-k"

    await session.refresh(err)                    # no new failure arrived -> verified as fixed
    err.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    rep = (await client.post("/api/v1/internal/recovery/run", headers=H)).json()
    assert rep["resolved"] == [str(err.id)]


async def test_gemini_outage_creates_one_grouped_error(session):
    for i in range(3):
        await record_error(session, source="gemini", operation="gemini", error_type=ErrorType.RATE_LIMIT,
                           risk=RiskLevel.SAFE, message="Gemini API error 429: quota", retryable=False,
                           payload={"last_message_id": str(i)})
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(AutomationError)) == 1
