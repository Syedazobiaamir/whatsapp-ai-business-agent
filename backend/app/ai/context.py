"""Builds everything the AI needs to know for one message ("context management").

Context = business info + relevant FAQs + service catalog + what we know about the lead
        + the last N messages (sliding window). Only RELEVANT FAQs are included so the prompt
stays small, cheap and focused.
"""
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Text, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, BusinessFAQ, Conversation, Customer, Lead, Message, Service
from app.models.enums import SenderType

DEFAULT_AI_CONFIG = {
    "model": None, "temperature": 0.3, "tone": "friendly, professional, concise",
    "max_history_messages": 12, "faq_confidence_threshold": 0.6,
    "handoff_keywords": ["human", "agent", "representative", "talk to someone"],
    "never_invent_prices": True,
}
DEFAULT_LEAD_RULES = {"hot_min_score": 70, "warm_min_score": 40}


@dataclass
class FAQHit:
    id: str
    question: str
    answer: str
    rank: float


@dataclass
class AgentContext:
    message: Message
    conversation: Conversation
    customer: Customer
    lead: Lead | None
    history: list[Message]
    faqs: list[FAQHit]
    services: list[Service]
    business_info: dict[str, Any]
    ai_config: dict[str, Any]
    lead_rules: dict[str, Any]
    all_faq_answers: list[str] = field(default_factory=list)   # for the price guardrail


async def search_faqs(session: AsyncSession, text: str, limit: int = 5) -> list[FAQHit]:
    """Full-text search with OR between words.

    plainto_tsquery("how much does an online store cost") = 'much' & 'onlin' & 'store' & 'cost'
    (AND: every word must match -> usually nothing). We swap & for | so ANY word can match,
    then ts_rank puts the FAQs matching the most words first.
    """
    if not text or not text.strip():
        return []
    tsq = func.to_tsquery("english", func.replace(
        cast(func.plainto_tsquery("english", text), Text), "&", "|"))
    rank = func.ts_rank(BusinessFAQ.search_vector, tsq).label("rank")
    rows = await session.execute(
        select(BusinessFAQ.id, BusinessFAQ.question, BusinessFAQ.answer, rank)
        .where(BusinessFAQ.is_active.is_(True), BusinessFAQ.search_vector.op("@@")(tsq))
        .order_by(desc("rank"), BusinessFAQ.priority.desc())
        .limit(limit)
    )
    return [FAQHit(str(r.id), r.question, r.answer, float(r.rank)) for r in rows]


async def _setting(session: AsyncSession, key: str, default: dict) -> dict:
    row = await session.get(AppSetting, key)
    return {**default, **(row.value if row else {})}


async def load_context(session: AsyncSession, message: Message) -> AgentContext:
    conversation = await session.get(Conversation, message.conversation_id)
    customer = await session.get(Customer, conversation.customer_id)
    lead = await session.scalar(select(Lead).where(Lead.customer_id == customer.id))
    ai_config = await _setting(session, "ai_config", DEFAULT_AI_CONFIG)

    # Sliding window: newest N messages up to and including this one, then oldest -> newest.
    n = int(ai_config.get("max_history_messages", 12))
    # Order by seq (insertion order, ONE clock). created_at mixes Meta's whole-second timestamps
    # (inbound) with our server clock (replies), which mis-orders messages within the same second.
    recent = (await session.scalars(
        select(Message).where(Message.conversation_id == conversation.id,
                              Message.seq <= message.seq)
        .order_by(Message.seq.desc()).limit(n)
    )).all()

    services = (await session.scalars(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.name))).all()
    all_answers = (await session.scalars(
        select(BusinessFAQ.answer).where(BusinessFAQ.is_active.is_(True)))).all()

    return AgentContext(
        message=message, conversation=conversation, customer=customer, lead=lead,
        history=list(reversed(recent)),
        faqs=await search_faqs(session, message.body or ""),
        services=list(services),
        business_info=await _setting(session, "business_info", {}),
        ai_config=ai_config,
        lead_rules=await _setting(session, "lead_rules", DEFAULT_LEAD_RULES),
        all_faq_answers=list(all_answers),
    )


_ROLE = {SenderType.CUSTOMER: "Customer", SenderType.AI: "Assistant",
         SenderType.HUMAN: "Team member", SenderType.SYSTEM: "System"}


def format_history(ctx: AgentContext) -> str:
    lines = []
    for m in ctx.history[:-1]:       # the latest message is shown separately
        lines.append(f"{_ROLE.get(m.sender_type, 'Unknown')}: {(m.body or '').strip()[:500]}")
    return "\n".join(lines) or "(no earlier messages)"


def format_services(ctx: AgentContext) -> str:
    def price(s: Service) -> str:
        if s.price_from is None:
            return "price on request"
        hi = f"-{s.price_to:,.0f}" if s.price_to is not None else "+"
        return f"{s.currency} {s.price_from:,.0f}{hi}"
    return "\n".join(f"- {s.name}: {s.description} ({price(s)})" for s in ctx.services) or "(none)"


def format_faqs(ctx: AgentContext) -> str:
    return "\n".join(f"[{f.id}] Q: {f.question}\n    A: {f.answer}" for f in ctx.faqs) \
        or "(no matching FAQ found)"


def format_lead(ctx: AgentContext) -> str:
    lead, c = ctx.lead, ctx.customer
    known = {
        "name": c.name, "email": c.email, "company": c.company,
        "requirements": lead.requirements if lead else None,
        "interested_services": ", ".join(lead.interested_services or []) if lead else None,
        "budget": lead.budget_text if lead else None,
        "timeline": lead.timeline if lead else None,
    }
    return "\n".join(f"- {k}: {v if v else 'unknown'}" for k, v in known.items())
