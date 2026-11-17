"""Stage 8 tests: monitoring endpoints used by the dashboard."""
import httpx
from pydantic import SecretStr

import app.api.v1.monitoring as monitoring
from app.config import get_settings
from app.main import app
from app.models import AutomationError
from app.models.enums import AgentRole, ErrorStatus, ErrorType, RiskLevel


def use_settings(**kw):
    s = get_settings().model_copy(update=kw)
    app.dependency_overrides[get_settings] = lambda: s


async def test_automation_errors_list_and_filter(client, session, make_agent):
    session.add_all([
        AutomationError(source="whatsapp", error_type=ErrorType.TEMPORARY, risk_level=RiskLevel.RISKY,
                        error_message="no answer", status=ErrorStatus.NEEDS_APPROVAL),
        AutomationError(source="gemini", error_type=ErrorType.RATE_LIMIT, risk_level=RiskLevel.SAFE,
                        error_message="429", status=ErrorStatus.RESOLVED),
    ])
    await session.commit()
    _, h = await make_agent(AgentRole.VIEWER)
    r = (await client.get("/api/v1/automation-errors", headers=h)).json()
    assert r["total"] == 2
    r = (await client.get("/api/v1/automation-errors", params={"status": "needs_approval"}, headers=h)).json()
    assert [e["source"] for e in r["items"]] == ["whatsapp"]


async def test_n8n_not_configured(client, make_agent):
    use_settings(n8n_api_key=SecretStr(""))
    _, h = await make_agent(AgentRole.VIEWER)
    r = (await client.get("/api/v1/monitoring/n8n", headers=h)).json()
    assert r["configured"] is False and "N8N_API_KEY" in r["hint"]


async def test_n8n_workflows_and_executions(client, make_agent, monkeypatch):
    seen = []

    def fake_n8n(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("/workflows"):
            return httpx.Response(200, json={"data": [{"id": "W1", "name": "WA 1", "active": True}]})
        return httpx.Response(200, json={"data": [
            {"id": "9", "workflowId": "W1", "status": "error", "mode": "webhook",
             "startedAt": "2026-11-16T10:00:00Z", "stoppedAt": "2026-11-16T10:00:01Z"}]})

    monkeypatch.setattr(monitoring, "n8n_transport", lambda: httpx.MockTransport(fake_n8n))
    use_settings(n8n_api_key=SecretStr("n8n-key"), n8n_base_url="http://n8n.test:5690")
    _, h = await make_agent(AgentRole.VIEWER)
    r = (await client.get("/api/v1/monitoring/n8n", headers=h)).json()
    assert r["reachable"] is True and r["workflows"][0]["active"] is True
    assert r["executions"][0] == {"id": "9", "workflow_id": "W1", "workflow_name": "WA 1", "status": "error",
                                  "mode": "webhook", "started_at": "2026-11-16T10:00:00Z",
                                  "stopped_at": "2026-11-16T10:00:01Z"}
    assert seen[0].headers["X-N8N-API-KEY"] == "n8n-key"        # key sent server-side only
    assert "n8n-key" not in str(r)                               # ...and never returned to the browser


async def test_n8n_unreachable(client, make_agent, monkeypatch):
    def down(_req):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(monitoring, "n8n_transport", lambda: httpx.MockTransport(down))
    use_settings(n8n_api_key=SecretStr("k"))
    _, h = await make_agent(AgentRole.VIEWER)
    r = (await client.get("/api/v1/monitoring/n8n", headers=h)).json()
    assert r["configured"] is True and r["reachable"] is False


async def test_follow_up_list_includes_customer_name(client, session, make_agent):
    from datetime import UTC, datetime

    from app.models import Customer, FollowUpTask, Lead
    c = Customer(wa_id="923001234567", name="Ali")
    session.add(c)
    await session.flush()
    lead = Lead(customer_id=c.id)
    session.add(lead)
    await session.flush()
    session.add(FollowUpTask(lead_id=lead.id, customer_id=c.id, scheduled_at=datetime.now(UTC),
                             template_name="hello_world", idempotency_key="k1"))
    await session.commit()
    _, h = await make_agent(AgentRole.VIEWER)
    item = (await client.get("/api/v1/follow-ups", headers=h)).json()["items"][0]
    assert (item["customer_name"], item["customer_wa_id"]) == ("Ali", "923001234567")
