"""Follow-up automation rules (used by n8n Workflow 4).

A follow-up is a MARKETING message to a lead who went quiet. It is only allowed when:
  * the customer opted in to marketing and has not opted out (consent),
  * the lead is hot/warm and still open (not won/lost),
  * a human is not currently handling the conversation,
  * we spoke last and the customer has been quiet long enough,
  * we have not hit the maximum number of follow-ups.
Outside WhatsApp's 24h window only an approved TEMPLATE may be sent.
Every check is repeated at send time, because things change between planning and sending.
"""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, Conversation, Customer, FollowUpTask, Lead, Message
from app.models.enums import (
    ActorType,
    ConversationMode,
    ConversationStatus,
    FollowUpStatus,
    LeadStatus,
    LeadTemperature,
    MessageDirection,
    MessageType,
    SendStatus,
    SenderType,
)
from app.services.leads import add_lead_activity
from app.services.messaging import record_send_failure, within_window
from app.services.whatsapp import WhatsAppClient, WhatsAppSendError

logger = logging.getLogger("app.followups")

DEFAULT_RULES = {"enabled": True, "first_follow_up_after_hours": 24, "second_follow_up_after_hours": 72,
                 "max_follow_ups": 2, "require_human_approval": True, "template_name": "lead_follow_up",
                 "template_uses_customer_name": False}


async def get_rules(session: AsyncSession) -> dict:
    row = await session.get(AppSetting, "follow_up_rules")
    return {**DEFAULT_RULES, **(row.value if row else {})}


def consent_ok(customer: Customer) -> bool:
    return customer.marketing_opt_in and customer.opted_out_at is None


async def plan_follow_ups(session: AsyncSession, now: datetime | None = None) -> list[FollowUpTask]:
    """Create follow-up tasks for eligible leads. Safe to run every hour (idempotency keys)."""
    now = now or datetime.now(UTC)
    rules = await get_rules(session)
    if not rules["enabled"]:
        return []

    rows = (await session.execute(
        select(Lead, Customer, Conversation)
        .join(Customer, Customer.id == Lead.customer_id)
        .join(Conversation, (Conversation.customer_id == Customer.id)
              & (Conversation.status == ConversationStatus.OPEN))
        .where(Lead.temperature.in_((LeadTemperature.HOT, LeadTemperature.WARM)),
               Lead.status.not_in((LeadStatus.WON, LeadStatus.LOST)),
               Customer.marketing_opt_in.is_(True), Customer.opted_out_at.is_(None),
               Conversation.mode == ConversationMode.AI)
    )).all()

    created: list[FollowUpTask] = []
    for lead, customer, conv in rows:
        last = await session.scalar(
            select(Message).where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc(), Message.seq.desc()).limit(1))
        if last is None or last.direction != MessageDirection.OUTBOUND:
            continue                                  # customer spoke last: no chasing needed
        done = await session.scalar(select(func.count()).select_from(FollowUpTask).where(
            FollowUpTask.lead_id == lead.id,
            FollowUpTask.status.in_((FollowUpStatus.SENT, FollowUpStatus.PENDING_APPROVAL,
                                     FollowUpStatus.APPROVED))))
        if done >= rules["max_follow_ups"]:
            continue
        wait = rules["first_follow_up_after_hours"] if done == 0 else rules["second_follow_up_after_hours"]
        if now - last.created_at < timedelta(hours=wait):
            continue

        number = done + 1
        use_template = not within_window(conv, now)
        task = FollowUpTask(
            lead_id=lead.id, customer_id=customer.id, scheduled_at=now,
            message_type=MessageType.TEMPLATE if use_template else MessageType.TEXT,
            template_name=rules["template_name"] if use_template else None,
            # Only templates with a {{1}} placeholder may receive a parameter (hello_world has none).
            template_params=({"body_parameters": [customer.name or "there"]}
                             if use_template and rules.get("template_uses_customer_name") else None),
            message_body=None if use_template else
            f"Hi {customer.name or 'there'}, just checking in about your project. "
            "Do you have any questions I can help with?",
            status=FollowUpStatus.PENDING_APPROVAL if rules["require_human_approval"] else FollowUpStatus.APPROVED,
            requires_approval=rules["require_human_approval"],
            # The key makes "follow-up #1 for this lead" impossible to create twice.
            idempotency_key=f"lead:{lead.id}:followup:{number}",
        )
        exists = await session.scalar(select(FollowUpTask.id).where(
            FollowUpTask.idempotency_key == task.idempotency_key))
        if exists:
            continue
        session.add(task)
        add_lead_activity(session, lead, activity_type="follow_up_planned", actor_type=ActorType.N8N,
                          description=f"Follow-up #{number} planned "
                                      f"({'template ' + task.template_name if use_template else 'text'})")
        created.append(task)
    await session.commit()
    return created


@dataclass
class SendOutcome:
    task_id: str
    status: str
    detail: str | None = None
    message_id: str | None = None


async def send_follow_up(session: AsyncSession, client: WhatsAppClient, task_id) -> SendOutcome:
    # FOR UPDATE SKIP LOCKED: if another worker is sending this task right now, skip it.
    task = await session.scalar(select(FollowUpTask).where(FollowUpTask.id == task_id)
                                .with_for_update(skip_locked=True))
    if task is None:
        exists = await session.get(FollowUpTask, task_id)
        if exists is None:
            return SendOutcome(str(task_id), "not_found")
        return SendOutcome(str(task_id), "in_progress")
    if task.status != FollowUpStatus.APPROVED:
        # Read the values BEFORE rollback: rollback expires loaded objects.
        outcome = SendOutcome(str(task.id), task.status.value, "not approved / already handled")
        await session.rollback()            # releases the row lock
        return outcome

    lead = await session.get(Lead, task.lead_id)
    customer = await session.get(Customer, task.customer_id)
    conv = await session.scalar(select(Conversation).where(
        Conversation.customer_id == customer.id, Conversation.status == ConversationStatus.OPEN))

    # Re-check every rule at send time.
    reason = None
    if not consent_ok(customer):
        reason = "customer has no marketing consent / opted out"
    elif lead.status in (LeadStatus.WON, LeadStatus.LOST):
        reason = f"lead is {lead.status.value}"
    elif conv is None or conv.mode == ConversationMode.HUMAN:
        reason = "a human is handling the conversation"
    elif conv.last_customer_message_at and conv.last_customer_message_at > task.created_at:
        reason = "customer replied after the follow-up was planned"
    elif task.message_type == MessageType.TEXT and not within_window(conv):
        reason = "24h window closed: a template is required"
    if reason:
        task.status, task.last_error = FollowUpStatus.SKIPPED, reason
        add_lead_activity(session, lead, activity_type="follow_up_skipped", actor_type=ActorType.N8N,
                          description=f"Follow-up skipped: {reason}")
        await session.commit()
        return SendOutcome(str(task.id), "skipped", reason)

    body = (task.message_body if task.message_type == MessageType.TEXT
            else f"[template:{task.template_name}]")
    msg = Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND,
                  sender_type=SenderType.SYSTEM, message_type=task.message_type, body=body,
                  send_status=SendStatus.PENDING)
    session.add(msg)
    task.attempts += 1
    await session.flush()
    try:
        if task.message_type == MessageType.TEMPLATE:
            values = (task.template_params or {}).get("body_parameters") or []
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": str(v)} for v in values]}] if values else None
            result = await client.send_template(customer.wa_id, task.template_name, components=components)
        else:
            result = await client.send_text(customer.wa_id, task.message_body or "")
    except WhatsAppSendError as exc:
        msg.send_status, msg.error_message = SendStatus.FAILED, str(exc)[:1000]
        task.last_error = str(exc)[:1000]
        # Retry later ONLY if Meta definitely did not get it and attempts remain.
        if exc.retryable and not exc.delivery_unknown and task.attempts < task.max_attempts:
            status = "retry_later"
        else:
            task.status = FollowUpStatus.FAILED
            status = "failed"
        await record_send_failure(session, exc, message=msg, conversation=conv, follow_up_task_id=task.id)
        await session.commit()
        return SendOutcome(str(task.id), status, str(exc)[:300], str(msg.id))

    msg.wa_message_id = result.wa_message_id
    msg.send_status = SendStatus.MOCKED if result.mocked else SendStatus.SENT
    task.status, task.sent_message_id = FollowUpStatus.SENT, msg.id
    conv.last_message_at = datetime.now(UTC)
    add_lead_activity(session, lead, activity_type="follow_up_sent", actor_type=ActorType.N8N,
                      description=f"Follow-up sent ({msg.send_status.value})")
    await session.commit()
    return SendOutcome(str(task.id), "sent", msg.send_status.value, str(msg.id))
