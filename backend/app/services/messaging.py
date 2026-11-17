"""Business logic for WhatsApp messages: store incoming, track delivery, send replies."""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AutomationError, Conversation, Customer, HumanAgent, Message
from app.models.enums import (
    ActorType,
    ConversationStatus,
    ErrorStatus,
    MessageDirection,
    MessageType,
    RiskLevel,
    SendStatus,
    SenderType,
)
from app.services.audit import record_audit
from app.services.whatsapp import (
    InboundMessage,
    StatusUpdate,
    WAWebhook,
    WhatsAppClient,
    WhatsAppSendError,
    extract_events,
)

logger = logging.getLogger("app.messaging")

WHATSAPP_WINDOW = timedelta(hours=24)
OPT_OUT_WORDS = {"STOP", "UNSUBSCRIBE"}
OPT_IN_WORDS = {"START", "SUBSCRIBE"}

# Delivery statuses only move forward: a late "delivered" must not overwrite "read".
_STATUS_RANK = {SendStatus.PENDING: 0, SendStatus.SENT: 1, SendStatus.DELIVERED: 2, SendStatus.READ: 3}


def within_window(conv: Conversation, now: datetime | None = None) -> bool:
    """WhatsApp rule: free-form messages only within 24h of the customer's last message."""
    last = conv.last_customer_message_at
    return last is not None and (now or datetime.now(UTC)) - last < WHATSAPP_WINDOW


# ------------------------------- Incoming -------------------------------

@dataclass
class WebhookResult:
    new_message_ids: list[uuid.UUID] = field(default_factory=list)   # for the AI step (Stage 6)
    duplicates: int = 0
    statuses_applied: int = 0
    ignored: int = 0


async def _get_or_create_customer(session: AsyncSession, msg: InboundMessage) -> Customer:
    # Upsert: create the customer, or (if they exist) refresh last_seen_at.
    # coalesce keeps a name a human already typed on the dashboard.
    stmt = (
        insert(Customer)
        .values(wa_id=msg.wa_id, name=msg.profile_name, last_seen_at=msg.sent_at)
        .on_conflict_do_update(
            index_elements=[Customer.wa_id],
            set_={
                "name": func.coalesce(Customer.name, msg.profile_name),
                "last_seen_at": func.greatest(Customer.last_seen_at, msg.sent_at),
                "updated_at": func.now(),
            },
        )
        .returning(Customer.id)
    )
    customer_id = await session.scalar(stmt)
    return await session.get(Customer, customer_id, populate_existing=True)


async def _get_or_create_open_conversation(session: AsyncSession, customer: Customer) -> Conversation:
    # ON CONFLICT DO NOTHING on the "one open conversation per customer" index:
    # two webhooks arriving at the same moment cannot create two conversations.
    await session.execute(
        insert(Conversation)
        .values(customer_id=customer.id, status=ConversationStatus.OPEN.value)
        # The predicate must be literal SQL identical to the index ("status = 'open'"). A bound
        # parameter works for the first 5 runs, then breaks once PostgreSQL uses a generic plan.
        .on_conflict_do_nothing(index_elements=[Conversation.customer_id],
                                index_where=text("status = 'open'"))
    )
    return await session.scalar(
        select(Conversation).where(Conversation.customer_id == customer.id,
                                   Conversation.status == ConversationStatus.OPEN)
        .execution_options(populate_existing=True)
    )


async def record_inbound(session: AsyncSession, msg: InboundMessage) -> tuple[Message | None, bool]:
    """Store one incoming message. Returns (message, is_duplicate)."""
    customer = await _get_or_create_customer(session, msg)
    conv = await _get_or_create_open_conversation(session, customer)

    # DUPLICATE PROTECTION: the UNIQUE wa_message_id + ON CONFLICT DO NOTHING means
    # a webhook Meta delivers twice is stored once, and RETURNING gives nothing the 2nd time.
    message_id = await session.scalar(
        insert(Message)
        .values(conversation_id=conv.id, direction=MessageDirection.INBOUND.value,
                sender_type=SenderType.CUSTOMER.value, wa_message_id=msg.wa_message_id,
                message_type=msg.message_type.value, body=msg.body,
                send_status=SendStatus.RECEIVED.value, created_at=msg.sent_at)
        .on_conflict_do_nothing(index_elements=[Message.wa_message_id])
        .returning(Message.id)
    )
    if message_id is None:
        return None, True

    # greatest(): events can arrive out of order; never move the 24h window backwards.
    conv.last_customer_message_at = func.greatest(Conversation.last_customer_message_at, msg.sent_at)
    conv.last_message_at = func.greatest(Conversation.last_message_at, msg.sent_at)

    keyword = (msg.body or "").strip().upper()
    if keyword in OPT_OUT_WORDS and customer.opted_out_at is None:
        customer.marketing_opt_in = False
        customer.opted_out_at = datetime.now(UTC)
        await record_audit(session, action="customer.opted_out", entity_type="customer",
                           entity_id=customer.id, actor_type=ActorType.CUSTOMER,
                           changes={"keyword": keyword})
    elif keyword in OPT_IN_WORDS:
        customer.marketing_opt_in = True
        customer.opted_out_at = None
        await record_audit(session, action="customer.opted_in", entity_type="customer",
                           entity_id=customer.id, actor_type=ActorType.CUSTOMER,
                           changes={"keyword": keyword})
    return await session.get(Message, message_id), False


async def apply_status(session: AsyncSession, status: StatusUpdate) -> bool:
    """Update the delivery status of a message we sent. Returns True if something changed."""
    msg = await session.scalar(select(Message).where(Message.wa_message_id == status.wa_message_id))
    if msg is None or msg.direction != MessageDirection.OUTBOUND:
        return False
    old, new = msg.send_status, status.status
    if new == SendStatus.FAILED:
        if old in (SendStatus.DELIVERED, SendStatus.READ):
            return False                      # already delivered: a stale "failed" is ignored
        msg.send_status, msg.error_message = new, status.error
        return True
    if _STATUS_RANK.get(new, -1) > _STATUS_RANK.get(old, -1):
        msg.send_status = new
        return True
    return False


async def process_webhook(session: AsyncSession, webhook: WAWebhook, settings: Settings) -> WebhookResult:
    result = WebhookResult()
    messages, statuses = extract_events(webhook)
    for msg in messages:
        # Ignore events for a different phone number than ours (if configured).
        if settings.wa_phone_number_id and msg.phone_number_id not in (None, settings.wa_phone_number_id):
            result.ignored += 1
            continue
        stored, duplicate = await record_inbound(session, msg)
        if duplicate:
            result.duplicates += 1
            logger.info("Duplicate webhook ignored: %s", msg.wa_message_id)
        elif stored:
            result.new_message_ids.append(stored.id)
    for st in statuses:
        if await apply_status(session, st):
            result.statuses_applied += 1
    await session.commit()
    return result


# ------------------------------- Outgoing -------------------------------

class OutsideWindowError(Exception):
    """More than 24h since the customer's last message: only templates are allowed."""


async def record_send_failure(session: AsyncSession, error: WhatsAppSendError, *,
                              message: Message, conversation: Conversation,
                              follow_up_task_id: uuid.UUID | None = None) -> AutomationError:
    """Log a failed send for monitoring + recovery. Never stores tokens or secrets.

    One row per failed message (unique_key), so each can be recovered on its own.
    Unknown delivery -> RISKY -> a human must approve any resend (no blind repeats).
    """
    from app.services.recovery import record_error

    payload = {"message_id": str(message.id), "conversation_id": str(conversation.id),
               "status_code": error.status_code, "meta_code": error.meta_code,
               "delivery_unknown": error.delivery_unknown}
    if follow_up_task_id:
        payload["follow_up_task_id"] = str(follow_up_task_id)
    row, _ = await record_error(
        session, source="whatsapp", operation="follow_up" if follow_up_task_id else "send_message",
        node="send_follow_up" if follow_up_task_id else "send_message",
        error_type=error.error_type, risk=RiskLevel.RISKY if error.delivery_unknown else RiskLevel.SAFE,
        message=str(error), retryable=error.retryable and not error.delivery_unknown,
        payload=payload, unique_key=str(follow_up_task_id or message.id),
    )
    return row


def new_outbound_message(
    conversation: Conversation, body: str, *, sender_type: SenderType,
    agent: HumanAgent | None = None, reply_to: Message | None = None,
    intent: str | None = None, ai_metadata: dict | None = None,
) -> Message:
    """Create (not send) an outbound text message in 'pending' state."""
    return Message(conversation_id=conversation.id, direction=MessageDirection.OUTBOUND,
                   sender_type=sender_type, sent_by_agent_id=agent.id if agent else None,
                   reply_to_message_id=reply_to.id if reply_to else None, intent=intent,
                   ai_metadata=ai_metadata, message_type=MessageType.TEXT, body=body,
                   send_status=SendStatus.PENDING)


async def deliver_message(session: AsyncSession, client: WhatsAppClient, msg: Message,
                          conversation: Conversation, customer: Customer) -> Message:
    """Send an already-saved pending message. Raises WhatsAppSendError (after recording it)."""
    try:
        result = await client.send_text(customer.wa_id, msg.body or "")
    except WhatsAppSendError as exc:
        msg.send_status = SendStatus.FAILED
        msg.error_message = ("[delivery unknown] " if exc.delivery_unknown else "") + str(exc)[:1000]
        await record_send_failure(session, exc, message=msg, conversation=conversation)
        await session.commit()
        raise
    msg.wa_message_id = result.wa_message_id
    msg.send_status = SendStatus.MOCKED if result.mocked else SendStatus.SENT
    await session.commit()
    await session.refresh(msg)
    return msg


async def send_text_reply(
    session: AsyncSession,
    client: WhatsAppClient,
    conversation: Conversation,
    customer: Customer,
    body: str,
    *,
    sender_type: SenderType,
    agent: HumanAgent | None = None,
) -> Message:
    """Send a free-form text reply and record it. Raises OutsideWindowError / WhatsAppSendError."""
    if not within_window(conversation):
        raise OutsideWindowError("The 24-hour window is closed; a template message is required")

    msg = new_outbound_message(conversation, body, sender_type=sender_type, agent=agent)
    session.add(msg)
    await session.execute(update(Conversation).where(Conversation.id == conversation.id)
                          .values(last_message_at=datetime.now(UTC)))
    # Save BEFORE calling Meta: if the app crashes mid-send we still know we tried,
    # and nothing will silently send it again.
    await session.commit()
    return await deliver_message(session, client, msg, conversation, customer)
