"""Automation Monitoring page: n8n workflow status + execution history, and automation errors.

The browser never talks to n8n directly: the backend calls n8n's public API with N8N_API_KEY,
so the key stays on the server (never in frontend code).
"""
import uuid
from datetime import datetime
from typing import Annotated, Any

from datetime import UTC

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AnyRole, PageParams, SessionDep, SettingsDep, StaffOnly, WhatsAppDep
from app.models import AutomationError, FollowUpTask
from app.models.enums import AgentRole, ErrorStatus, ErrorType, FollowUpStatus, RiskLevel
from app.schemas.common import ORMModel, Page
from app.services.audit import paginate, record_audit
from app.services.recovery import resolve, run_recovery

router = APIRouter(tags=["monitoring"])


class AutomationErrorOut(ORMModel):
    id: uuid.UUID
    source: str
    workflow_name: str | None
    execution_id: str | None
    node_name: str | None
    error_type: ErrorType
    risk_level: RiskLevel
    error_message: str
    payload: dict[str, Any] | None
    status: ErrorStatus
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime
    operation: str | None
    occurrences: int
    last_seen_at: datetime | None
    alerted_at: datetime | None
    workflow_id: str | None


@router.get("/automation-errors", response_model=Page[AutomationErrorOut])
async def list_errors(_: AnyRole, session: SessionDep, pg: PageParams,
                      error_status: Annotated[ErrorStatus | None, Query(alias="status")] = None,
                      source: str | None = None):
    stmt = select(AutomationError).order_by(AutomationError.created_at.desc())
    if error_status:
        stmt = stmt.where(AutomationError.status == error_status)
    if source:
        stmt = stmt.where(AutomationError.source == source)
    items, total = await paginate(session, stmt, pg.page, pg.size)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


class ResolutionIn(BaseModel):
    note: str = Field(default="", max_length=1000)


async def _get_unresolved(session, error_id) -> AutomationError:
    row = await session.get(AutomationError, error_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Error not found")
    if row.status in (ErrorStatus.RESOLVED, ErrorStatus.IGNORED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already {row.status.value}")
    return row


@router.post("/automation-errors/{error_id}/approve-retry", response_model=AutomationErrorOut)
async def approve_retry(error_id: uuid.UUID, agent: StaffOnly, session: SessionDep, settings: SettingsDep,
                        wa: WhatsAppDep, request: Request):
    """A human explicitly decides to retry. RISKY retries (may duplicate a message) need an admin."""
    row = await _get_unresolved(session, error_id)
    if row.risk_level == RiskLevel.RISKY and agent.role != AgentRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can approve a risky retry")
    if row.operation not in ("send_message", "follow_up", "n8n_execution"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This error cannot be retried; fix the cause and resolve it")
    old_status = row.status.value
    await record_audit(session, action="automation_error.retry_approved", entity_type="automation_error",
                       entity_id=row.id, actor=agent, request=request,
                       changes={"risk_level": row.risk_level.value, "status": old_status,
                                "operation": row.operation})
    if row.operation == "follow_up":
        task = await session.get(FollowUpTask, uuid.UUID((row.payload or {})["follow_up_task_id"]))
        if task and task.status in (FollowUpStatus.FAILED, FollowUpStatus.APPROVED):
            task.status, task.max_attempts = FollowUpStatus.APPROVED, max(task.max_attempts, task.attempts + 1)
        row.status, row.risk_level, row.next_retry_at = ErrorStatus.RETRYING, RiskLevel.SAFE, datetime.now(UTC)
        await session.commit()                  # Workflow 4 sends it on its next run
    else:
        # The human took responsibility: run it now through the normal (safe) recovery path.
        row.status, row.risk_level, row.next_retry_at = ErrorStatus.RETRYING, RiskLevel.SAFE, datetime.now(UTC)
        await session.commit()
        await run_recovery(session, wa, settings)
    await session.refresh(row)
    return row


@router.post("/automation-errors/{error_id}/resolve", response_model=AutomationErrorOut)
async def resolve_error(error_id: uuid.UUID, body: ResolutionIn, agent: StaffOnly, session: SessionDep,
                        request: Request):
    row = await _get_unresolved(session, error_id)
    resolve(row, body.note or f"Resolved by {agent.full_name}", agent.id)
    await record_audit(session, action="automation_error.resolved", entity_type="automation_error",
                       entity_id=row.id, actor=agent, request=request, changes={"note": body.note})
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/automation-errors/{error_id}/ignore", response_model=AutomationErrorOut)
async def ignore_error(error_id: uuid.UUID, body: ResolutionIn, agent: StaffOnly, session: SessionDep,
                       request: Request):
    row = await _get_unresolved(session, error_id)
    row.status, row.resolved_at, row.next_retry_at = ErrorStatus.IGNORED, datetime.now(UTC), None
    row.resolution_note, row.resolved_by_agent_id = (body.note or "Ignored")[:1000], agent.id
    await record_audit(session, action="automation_error.ignored", entity_type="automation_error",
                       entity_id=row.id, actor=agent, request=request, changes={"note": body.note})
    await session.commit()
    await session.refresh(row)
    return row


def n8n_transport() -> httpx.AsyncBaseTransport | None:
    return None     # tests replace this with a fake n8n


@router.get("/monitoring/n8n")
async def n8n_status(_: AnyRole, settings: SettingsDep):
    """Workflows (active or not) + the last 20 executions, read from n8n's public REST API."""
    key = settings.n8n_api_key.get_secret_value()
    base = settings.n8n_base_url.rstrip("/")
    if not key:
        return {"configured": False, "reachable": None, "workflows": [], "executions": [],
                "hint": "Create an API key in n8n (Settings > n8n API) and set N8N_API_KEY in .env"}
    headers = {"X-N8N-API-KEY": key, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(base_url=f"{base}/api/v1", headers=headers, timeout=10,
                                     transport=n8n_transport()) as client:
            wf = await client.get("/workflows", params={"limit": 50})
            ex = await client.get("/executions", params={"limit": 20, "includeData": "false"})
        wf.raise_for_status()
        ex.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        return {"configured": True, "reachable": False, "workflows": [], "executions": [],
                "hint": f"Could not read n8n API: {type(exc).__name__}"}

    workflows = [{"id": w.get("id"), "name": w.get("name"), "active": bool(w.get("active")),
                  "updated_at": w.get("updatedAt")} for w in wf.json().get("data", [])]
    names = {w["id"]: w["name"] for w in workflows}
    executions = [{"id": e.get("id"), "workflow_id": e.get("workflowId"),
                   "workflow_name": names.get(e.get("workflowId"), e.get("workflowId")),
                   "status": e.get("status") or ("success" if e.get("finished") else "unknown"),
                   "mode": e.get("mode"), "started_at": e.get("startedAt"), "stopped_at": e.get("stoppedAt")}
                  for e in ex.json().get("data", [])]
    return {"configured": True, "reachable": True, "workflows": workflows, "executions": executions,
            "hint": None}
