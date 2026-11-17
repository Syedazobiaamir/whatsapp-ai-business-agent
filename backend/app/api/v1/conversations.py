"""Conversations: list, view history, take over from the AI, give back to the AI."""
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import AnyRole, PageParams, SessionDep, StaffOnly, WhatsAppDep
from app.models import Conversation, Customer, HandoffRequest, Message
from app.models.enums import (
    ActorType,
    ConversationMode,
    ConversationStatus,
    HandoffStatus,
    SenderType,
)
from app.schemas.common import Page
from app.schemas.crm import (
    ConversationDetail,
    ConversationOut,
    MessageOut,
    ReplyRequest,
    TakeoverRequest,
)
from app.services.audit import paginate, record_audit
from app.services.messaging import OutsideWindowError, send_text_reply, within_window
from app.services.whatsapp import WhatsAppSendError

router = APIRouter(prefix="/conversations", tags=["conversations"])

ACTIVE_HANDOFF = (HandoffStatus.PENDING, HandoffStatus.ACCEPTED)


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(
    _: AnyRole,
    session: SessionDep,
    pg: PageParams,
    conv_status: Annotated[ConversationStatus | None, Query(alias="status")] = None,
    mode: ConversationMode | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
):
    stmt = (
        select(Conversation).join(Customer).options(selectinload(Conversation.customer))
        .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.created_at.desc())
    )
    if conv_status:
        stmt = stmt.where(Conversation.status == conv_status)
    if mode:
        stmt = stmt.where(Conversation.mode == mode)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.wa_id.ilike(like)))
    convs, total = await paginate(session, stmt, pg.page, pg.size)

    # Two extra queries for the whole page (not per row): last message + pending handoffs.
    ids = [c.id for c in convs]
    previews: dict[uuid.UUID, str | None] = {}
    handoffs: set[uuid.UUID] = set()
    if ids:
        last_msgs = await session.execute(
            select(Message.conversation_id, Message.body)
            .where(Message.conversation_id.in_(ids))
            .distinct(Message.conversation_id)               # PostgreSQL DISTINCT ON
            .order_by(Message.conversation_id, Message.created_at.desc(), Message.seq.desc())
        )
        previews = {cid: (body or "")[:120] for cid, body in last_msgs}
        handoffs = set((await session.scalars(
            select(HandoffRequest.conversation_id).where(
                HandoffRequest.conversation_id.in_(ids), HandoffRequest.status.in_(ACTIVE_HANDOFF))
        )).all())

    items = [
        ConversationOut.model_validate(c).model_copy(update={
            "last_message_preview": previews.get(c.id),
            "has_pending_handoff": c.id in handoffs,
        })
        for c in convs
    ]
    return Page(items=items, total=total, page=pg.page, size=pg.size)


async def _load(session, conversation_id: uuid.UUID, with_messages: bool = False) -> Conversation:
    opts = [selectinload(Conversation.customer)]
    if with_messages:
        opts.append(selectinload(Conversation.messages))
    conv = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id).options(*opts)
        .execution_options(populate_existing=True)
    )
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conv


async def _detail(session, conversation_id: uuid.UUID) -> ConversationDetail:
    conv = await _load(session, conversation_id, with_messages=True)
    active = await session.scalar(
        select(HandoffRequest.id).where(HandoffRequest.conversation_id == conv.id,
                                        HandoffRequest.status.in_(ACTIVE_HANDOFF))
    )
    base = ConversationOut.model_validate(conv).model_dump()
    return ConversationDetail(
        **base | {"has_pending_handoff": active is not None},
        messages=[MessageOut.model_validate(m) for m in conv.messages],
        within_24h_window=within_window(conv),
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, _: AnyRole, session: SessionDep):
    return await _detail(session, conversation_id)


@router.post("/{conversation_id}/takeover", response_model=ConversationDetail)
async def take_over(
    conversation_id: uuid.UUID, agent: StaffOnly, session: SessionDep, request: Request,
    body: TakeoverRequest | None = None,
):
    """A human takes control: the AI stops replying to this conversation."""
    conv = await _load(session, conversation_id)
    if conv.status == ConversationStatus.CLOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversation is closed")
    reason = body.reason if body else "Taken over from dashboard"
    now = datetime.now(UTC)

    conv.mode = ConversationMode.HUMAN
    conv.assigned_agent_id = agent.id
    handoff = await session.scalar(
        select(HandoffRequest).where(HandoffRequest.conversation_id == conv.id,
                                     HandoffRequest.status.in_(ACTIVE_HANDOFF))
    )
    if handoff is None:          # no customer/AI request yet: create one for the record
        handoff = HandoffRequest(conversation_id=conv.id, requested_by=ActorType.AGENT, reason=reason)
        session.add(handoff)
    handoff.status = HandoffStatus.ACCEPTED
    handoff.assigned_agent_id = agent.id
    handoff.accepted_at = now

    await record_audit(session, action="conversation.takeover", entity_type="conversation",
                       entity_id=conv.id, actor=agent, request=request,
                       changes={"mode": ["ai", "human"], "reason": reason})
    await session.commit()
    return await _detail(session, conv.id)


@router.post("/{conversation_id}/reply", response_model=MessageOut,
             status_code=status.HTTP_201_CREATED)
async def human_reply(conversation_id: uuid.UUID, body: ReplyRequest, agent: StaffOnly,
                      session: SessionDep, wa: WhatsAppDep, request: Request):
    """A human sends a WhatsApp message. Requires taking over first (AI and human never both talk)."""
    conv = await _load(session, conversation_id)
    if conv.status == ConversationStatus.CLOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversation is closed")
    if conv.mode != ConversationMode.HUMAN:
        raise HTTPException(status.HTTP_409_CONFLICT, "Take over the conversation before replying")
    try:
        msg = await send_text_reply(session, wa, conv, conv.customer, body.text,
                                    sender_type=SenderType.HUMAN, agent=agent)
    except OutsideWindowError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except WhatsAppSendError as exc:
        # 502 = "the service we depend on failed". The failure is stored for monitoring.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "message": "WhatsApp could not send the message",
            "error_type": exc.error_type.value,
            "delivery_unknown": exc.delivery_unknown,
        }) from None
    await record_audit(session, action="conversation.human_reply", entity_type="conversation",
                       entity_id=conv.id, actor=agent, request=request,
                       changes={"message_id": str(msg.id), "send_status": msg.send_status.value})
    await session.commit()
    return msg


@router.post("/{conversation_id}/resume", response_model=ConversationDetail)
async def resume_ai(conversation_id: uuid.UUID, agent: StaffOnly, session: SessionDep, request: Request):
    """Hand the conversation back to the AI and close the active handoff."""
    conv = await _load(session, conversation_id)
    if conv.mode == ConversationMode.AI:
        raise HTTPException(status.HTTP_409_CONFLICT, "AI is already handling this conversation")
    conv.mode = ConversationMode.AI
    handoff = await session.scalar(
        select(HandoffRequest).where(HandoffRequest.conversation_id == conv.id,
                                     HandoffRequest.status.in_(ACTIVE_HANDOFF))
    )
    if handoff:
        handoff.status = HandoffStatus.RESOLVED
        handoff.resolved_at = datetime.now(UTC)
        handoff.resolution_note = f"AI resumed by {agent.full_name}"
    await record_audit(session, action="conversation.resume_ai", entity_type="conversation",
                       entity_id=conv.id, actor=agent, request=request,
                       changes={"mode": ["human", "ai"]})
    await session.commit()
    return await _detail(session, conv.id)
