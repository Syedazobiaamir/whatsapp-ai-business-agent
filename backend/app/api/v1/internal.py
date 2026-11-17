"""Machine-to-machine endpoints for n8n (header X-Internal-Api-Key).

Every endpoint is IDEMPOTENT: n8n may retry a request (timeouts, restarts), and calling an
endpoint twice must never send a second WhatsApp message or a second notification.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.deps import InternalOnly, SessionDep, SettingsDep, WhatsAppDep
from app.models import (AutomationError, Conversation, Customer, FollowUpTask, HandoffRequest, HumanAgent, Lead,
                        LeadActivity, Message)
from app.models.enums import (ActorType, ErrorStatus, ErrorType, FollowUpStatus, MessageDirection, RiskLevel,
                              SendStatus)
from app.services.audit import record_audit
from app.services.followups import plan_follow_ups, send_follow_up
from app.services.recovery import (
    SAFE_RETRY_WORKFLOWS,
    alert_text,
    classify_message,
    record_error,
    run_recovery,
)
from app.services.handoffs import assign_handoff, auto_resume
from app.services.leads import add_lead_activity
from app.services.messaging import deliver_message
from app.services.whatsapp import WhatsAppSendError

router = APIRouter(prefix="/internal", tags=["internal (n8n)"], dependencies=[InternalOnly])


def _dashboard(settings, path: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}{path}"


# ------------------------------------------------------------------ messages
@router.post("/messages/{message_id}/send")
async def send_pending_message(message_id: uuid.UUID, session: SessionDep, wa: WhatsAppDep):
    """Send a 'pending' outbound message (the AI reply). Calling it again never re-sends."""
    # Atomic claim: only a message that is pending AND unclaimed can be taken.
    claimed = await session.scalar(
        update(Message)
        .where(Message.id == message_id, Message.direction == MessageDirection.OUTBOUND,
               Message.send_status == SendStatus.PENDING, Message.processing_started_at.is_(None))
        .values(processing_started_at=datetime.now(UTC)).returning(Message.id))
    await session.commit()
    msg = await session.get(Message, message_id, populate_existing=True)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if not claimed:      # already sent / failed / being sent: report, do not send again
        return {"message_id": str(msg.id), "send_status": msg.send_status.value,
                "wa_message_id": msg.wa_message_id, "resent": False}
    conv = await session.get(Conversation, msg.conversation_id)
    customer = await session.get(Customer, conv.customer_id)
    try:
        msg = await deliver_message(session, wa, msg, conv, customer)
    except WhatsAppSendError as exc:
        return {"message_id": str(msg.id), "send_status": "failed", "error_type": exc.error_type.value,
                "delivery_unknown": exc.delivery_unknown, "resent": False}
    return {"message_id": str(msg.id), "send_status": msg.send_status.value,
            "wa_message_id": msg.wa_message_id, "resent": False}


# ------------------------------------------------------------------ leads (Workflow 2)
@router.get("/leads/{lead_id}/notification")
async def lead_notification(lead_id: uuid.UUID, session: SessionDep, settings: SettingsDep):
    """Everything the 'hot lead' email needs, plus already_notified to avoid duplicates."""
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    customer = await session.get(Customer, lead.customer_id)
    notified = await session.scalar(select(LeadActivity.id).where(
        LeadActivity.lead_id == lead.id, LeadActivity.activity_type == "sales_notified").limit(1))
    name = customer.name or customer.wa_id
    services = ", ".join(lead.interested_services or []) or "not specified"
    body = (
        f"New HOT lead: {name}\n\n"
        f"WhatsApp: +{customer.wa_id}\nEmail: {customer.email or '-'}\nCompany: {customer.company or '-'}\n"
        f"Score: {lead.score}/100\nWhy: {lead.qualification_reason or '-'}\n\n"
        f"Needs: {lead.requirements or '-'}\nServices: {services}\n"
        f"Budget: {lead.budget_text or '-'}\nTimeline: {lead.timeline or '-'}\n\n"
        f"Open in dashboard: {_dashboard(settings, f'/leads/{lead.id}')}"
    )
    return {"lead_id": str(lead.id), "already_notified": notified is not None,
            "temperature": lead.temperature.value, "to": settings.sales_notify_email,
            "subject": f"🔥 Hot lead: {name} ({lead.score}/100)", "body": body}


class NotifiedIn(BaseModel):
    channel: str = Field(max_length=32)      # gmail | log
    to: str = Field(max_length=255)


@router.post("/leads/{lead_id}/notified")
async def mark_lead_notified(lead_id: uuid.UUID, body: NotifiedIn, session: SessionDep):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    add_lead_activity(session, lead, activity_type="sales_notified", actor_type=ActorType.N8N,
                      description=f"Sales team notified via {body.channel} ({body.to})")
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------ handoffs (Workflow 3)
@router.post("/handoffs/{handoff_id}/assign")
async def assign(handoff_id: uuid.UUID, session: SessionDep, settings: SettingsDep):
    handoff = await session.get(HandoffRequest, handoff_id)
    if handoff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found")
    agent = await assign_handoff(session, handoff)
    conv = await session.get(Conversation, handoff.conversation_id)
    customer = await session.get(Customer, conv.customer_id)
    last = (await session.scalars(select(Message).where(Message.conversation_id == conv.id)
                                  .order_by(Message.created_at.desc(), Message.seq.desc()).limit(5))).all()
    transcript = "\n".join(f"{m.sender_type.value}: {(m.body or '')[:200]}" for m in reversed(last))
    to = agent.email if agent else settings.admin_alert_email
    return {
        "handoff_id": str(handoff.id), "status": handoff.status.value, "reason": handoff.reason,
        "conversation_mode": conv.mode.value,
        "agent": {"id": str(agent.id), "name": agent.full_name, "email": agent.email} if agent else None,
        "to": to,
        "subject": f"WhatsApp handoff: {customer.name or customer.wa_id} needs a person",
        "body": (f"{customer.name or 'A customer'} (+{customer.wa_id}) needs a team member.\n"
                 f"Reason: {handoff.reason}\n\nLast messages:\n{transcript}\n\n"
                 f"The AI is paused for this chat. Reply from the dashboard:\n"
                 f"{_dashboard(settings, f'/conversations/{conv.id}')}"),
    }


@router.get("/conversations/{conversation_id}/assignee")
async def conversation_assignee(conversation_id: uuid.UUID, session: SessionDep, settings: SettingsDep):
    """Who should hear about a new customer message while a human handles the chat?"""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    customer = await session.get(Customer, conv.customer_id)
    agent = await session.get(HumanAgent, conv.assigned_agent_id) if conv.assigned_agent_id else None
    last = await session.scalar(select(Message).where(Message.conversation_id == conv.id,
                                                      Message.direction == MessageDirection.INBOUND)
                                .order_by(Message.created_at.desc(), Message.seq.desc()).limit(1))
    return {"to": agent.email if agent else settings.admin_alert_email,
            "subject": f"New WhatsApp message from {customer.name or customer.wa_id}",
            "body": f"{customer.name or customer.wa_id}: {(last.body if last else '')[:500]}\n\n"
                    f"Reply: {_dashboard(settings, f'/conversations/{conv.id}')}"}


@router.post("/handoffs/auto-resume")
async def handoffs_auto_resume(session: SessionDep):
    resumed = await auto_resume(session)
    for conv in resumed:
        await record_audit(session, action="conversation.auto_resume_ai", entity_type="conversation",
                           entity_id=conv.id, actor_type=ActorType.N8N)
    await session.commit()
    return {"resumed": [str(c.id) for c in resumed]}


# ------------------------------------------------------------------ follow-ups (Workflow 4)
@router.post("/follow-ups/plan")
async def follow_ups_plan(session: SessionDep):
    created = await plan_follow_ups(session)
    return {"created": len(created), "task_ids": [str(t.id) for t in created]}


@router.get("/follow-ups/due")
async def follow_ups_due(session: SessionDep):
    tasks = (await session.scalars(select(FollowUpTask.id).where(
        FollowUpTask.status == FollowUpStatus.APPROVED,
        FollowUpTask.scheduled_at <= datetime.now(UTC)).limit(50))).all()
    return {"task_ids": [str(t) for t in tasks]}


@router.post("/follow-ups/{task_id}/send")
async def follow_up_send(task_id: uuid.UUID, session: SessionDep, wa: WhatsAppDep):
    outcome = await send_follow_up(session, wa, task_id)
    if outcome.status == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return outcome.__dict__


# ------------------------------------------------------------------ errors & recovery (Workflow 5)
class N8nErrorIn(BaseModel):
    """What n8n's Error Trigger knows about a failed execution."""
    workflow_id: str | None = Field(default=None, max_length=64)
    workflow_name: str | None = Field(default=None, max_length=160)
    execution_id: str | None = Field(default=None, max_length=64)
    node_name: str | None = Field(default=None, max_length=160)
    error_message: str = Field(default="Unknown error", max_length=4000)
    http_code: int | None = None
    mode: str | None = Field(default=None, max_length=32)


@router.post("/automation-errors")
async def report_n8n_error(body: N8nErrorIn, session: SessionDep, settings: SettingsDep):
    """Called by WA 5 (Error Trigger). Classifies, groups, and decides retry / alert / approval."""
    error_type = classify_message(body.error_message, body.http_code)
    safe_workflow = body.workflow_id in SAFE_RETRY_WORKFLOWS
    retryable = error_type in (ErrorType.TEMPORARY, ErrorType.RATE_LIMIT) and bool(body.execution_id)
    risk = RiskLevel.SAFE if safe_workflow or not retryable else RiskLevel.RISKY
    row, is_new = await record_error(
        session, source="n8n", operation="n8n_execution", error_type=error_type, risk=risk,
        message=body.error_message, retryable=retryable and safe_workflow, node=body.node_name,
        workflow_name=body.workflow_name, workflow_id=body.workflow_id, execution_id=body.execution_id,
        payload={"http_code": body.http_code, "mode": body.mode})
    await session.commit()
    await session.refresh(row)
    alert = row.alerted_at is None and row.status in (ErrorStatus.OPEN, ErrorStatus.NEEDS_APPROVAL)
    return {"error_id": str(row.id), "new": is_new, "status": row.status.value, "error_type": error_type.value,
            "risk_level": row.risk_level.value, "occurrences": row.occurrences, "alert": alert,
            **(alert_text(row, settings) if alert else {})}


@router.get("/recovery/alerts")
async def pending_alerts(session: SessionDep, settings: SettingsDep):
    """Errors that need a human and have not been announced yet (one alert per error group)."""
    rows = (await session.scalars(select(AutomationError).where(
        AutomationError.alerted_at.is_(None),
        AutomationError.status.in_((ErrorStatus.OPEN, ErrorStatus.NEEDS_APPROVAL)))
        .order_by(AutomationError.created_at).limit(20))).all()
    return {"alerts": [{"error_id": str(r.id), **alert_text(r, settings)} for r in rows]}


@router.post("/automation-errors/{error_id}/alerted")
async def mark_alerted(error_id: uuid.UUID, session: SessionDep):
    row = await session.get(AutomationError, error_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Error not found")
    row.alerted_at = row.alerted_at or datetime.now(UTC)
    await session.commit()
    return {"ok": True}


@router.post("/recovery/run")
async def recovery_run(session: SessionDep, settings: SettingsDep, wa: WhatsAppDep):
    """Called every 5 min by WA 5: retries SAFE errors that are due. Never touches risky ones."""
    report = await run_recovery(session, wa, settings)
    return report.__dict__


# ------------------------------------------------------------------ notifications
class NotificationLog(BaseModel):
    channel: str = Field(max_length=32)
    to: str = Field(max_length=255)
    subject: str = Field(max_length=300)
    body: str = Field(max_length=5000)
    related: dict[str, Any] | None = None


@router.post("/notifications/log")
async def log_notification(body: NotificationLog, session: SessionDep):
    """'log' notification channel: used until Gmail is connected, and visible in the audit log."""
    await record_audit(session, action="notification.sent", entity_type="notification",
                       actor_type=ActorType.N8N, changes=body.model_dump())
    await session.commit()
    return {"ok": True}
