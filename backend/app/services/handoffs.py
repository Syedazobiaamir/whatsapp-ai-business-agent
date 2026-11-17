"""Human handoff helpers (used by n8n Workflow 3)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, Conversation, HandoffRequest, HumanAgent, Message
from app.models.enums import AgentRole, ConversationMode, HandoffStatus, MessageDirection, SenderType

ACTIVE = (HandoffStatus.PENDING, HandoffStatus.ACCEPTED)
DEFAULT_RULES = {"auto_resume_after_hours": 24}


async def pick_agent(session: AsyncSession) -> HumanAgent | None:
    """Least-busy active sales agent (fewest active handoffs). Admins only if no sales agents."""
    busy = (select(HandoffRequest.assigned_agent_id, func.count().label("n"))
            .where(HandoffRequest.status.in_(ACTIVE)).group_by(HandoffRequest.assigned_agent_id)
            .subquery())
    for roles in ((AgentRole.SALES_AGENT,), (AgentRole.ADMIN,)):
        agent = await session.scalar(
            select(HumanAgent).outerjoin(busy, busy.c.assigned_agent_id == HumanAgent.id)
            .where(HumanAgent.is_active.is_(True), HumanAgent.role.in_(roles))
            .order_by(func.coalesce(busy.c.n, 0), HumanAgent.created_at).limit(1))
        if agent:
            return agent
    return None


async def assign_handoff(session: AsyncSession, handoff: HandoffRequest) -> HumanAgent | None:
    """Assign once (idempotent): calling again returns the same agent."""
    conv = await session.get(Conversation, handoff.conversation_id)
    if handoff.assigned_agent_id:
        return await session.get(HumanAgent, handoff.assigned_agent_id)
    agent = await pick_agent(session)
    if agent:
        handoff.assigned_agent_id = agent.id
        conv.assigned_agent_id = agent.id
        await session.commit()
    return agent


async def auto_resume(session: AsyncSession, now: datetime | None = None) -> list[Conversation]:
    """Give a conversation back to the AI only when it is clearly safe:
    in human mode for longer than the limit AND nobody (customer or team) wrote since then."""
    now = now or datetime.now(UTC)
    row = await session.get(AppSetting, "handoff_rules")
    rules = {**DEFAULT_RULES, **(row.value if row else {})}
    cutoff = now - timedelta(hours=int(rules["auto_resume_after_hours"]))

    convs = (await session.scalars(select(Conversation).where(
        Conversation.mode == ConversationMode.HUMAN,
        func.coalesce(Conversation.last_message_at, Conversation.updated_at) < cutoff))).all()
    resumed = []
    for conv in convs:
        recent_human = await session.scalar(select(Message.id).where(
            Message.conversation_id == conv.id, Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == SenderType.HUMAN, Message.created_at >= cutoff).limit(1))
        if recent_human:
            continue
        conv.mode = ConversationMode.AI
        for h in (await session.scalars(select(HandoffRequest).where(
                HandoffRequest.conversation_id == conv.id, HandoffRequest.status.in_(ACTIVE)))).all():
            h.status, h.resolved_at = HandoffStatus.RESOLVED, now
            h.resolution_note = f"AI auto-resumed after {rules['auto_resume_after_hours']}h of inactivity"
        resumed.append(conv)
    await session.commit()
    return resumed
