"""The Orchestrator: plain, predictable code that decides which AI module runs and what happens.

It is deliberately NOT an "autonomous agent". The flow for one inbound message:

  claim message (no double processing)
    -> human mode?          -> stay silent (a person is handling it)
    -> STOP / START?        -> confirm opt-out / opt-in (no AI needed)
    -> asks for a human?    -> handoff (no AI needed)
    -> SupportAgent         -> intent + FAQ answer
         complaint/human    -> handoff
         sales intent       -> SalesAgent -> lead facts -> LeadQualification (rules)
         low confidence     -> handoff (escalate unresolved query)
    -> guardrails           -> no unapproved prices
  save everything in ONE transaction, then send the reply (mock or real WhatsApp)
"""
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents import SalesAgent, SupportAgent
from app.ai.context import AgentContext, load_context
from app.ai.guardrails import (
    SAFE_PRICE_REPLY,
    allowed_amounts,
    clean_reply,
    find_unapproved_amounts,
)
from app.ai.lead_qualification import qualify, to_decimal
from app.ai.llm import LLMClient, LLMError
from app.ai.schemas import SALES_INTENTS, LeadFacts
from app.models import Customer, HandoffRequest, Lead, Message
from app.models.enums import (
    ActorType,
    ConversationMode,
    HandoffStatus,
    LeadTemperature,
    MessageDirection,
    RiskLevel,
    SenderType,
)
from app.schemas.agent import AgentDecision, LeadSummary
from app.services.leads import add_lead_activity
from app.services.messaging import OPT_IN_WORDS, OPT_OUT_WORDS, deliver_message, new_outbound_message
from app.services.recovery import record_error
from app.services.whatsapp import WhatsAppClient, WhatsAppSendError

logger = logging.getLogger("app.ai.orchestrator")

CLAIM_TIMEOUT = timedelta(minutes=5)     # a crashed worker's claim expires after this
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MessageNotFound(Exception):
    pass


class ProcessingInProgress(Exception):
    pass


@dataclass
class Plan:
    """What we intend to do. Built first (no DB writes), then applied in one transaction."""
    intent: str | None = None
    confidence: float | None = None
    reply: str | None = None
    handoff_reason: str | None = None
    handoff_by: ActorType = ActorType.AI
    skip_reason: str | None = None
    facts: LeadFacts | None = None
    modules: list[str] = field(default_factory=list)
    guardrails: list[str] = field(default_factory=list)
    fallback_used: bool = False
    llm_error: LLMError | None = None
    used_faq_ids: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(self, session: AsyncSession, llm_factory: Callable[[str | None], LLMClient],
                 wa_client: WhatsAppClient):
        self.session = session
        self.llm_factory = llm_factory
        self.wa = wa_client

    # ------------------------------------------------------------------ public
    async def process(self, message_id: uuid.UUID, send: bool = True) -> AgentDecision:
        msg = await self.session.get(Message, message_id)
        if msg is None:
            raise MessageNotFound(str(message_id))
        if msg.direction != MessageDirection.INBOUND:
            return await self._skipped(msg, "not_inbound")
        if msg.processed_at is not None:
            return await self._already_processed(msg)
        if not await self._claim(msg.id):
            await self.session.refresh(msg)
            if msg.processed_at is not None:
                return await self._already_processed(msg)
            raise ProcessingInProgress(str(message_id))

        try:
            await self.session.refresh(msg)
            ctx = await load_context(self.session, msg)
            plan = await self._decide(ctx)
            decision, reply = await self._apply(ctx, plan)
        except Exception:
            # Release the claim so a retry can process it (nothing was sent).
            await self.session.rollback()
            await self.session.execute(update(Message).where(Message.id == message_id)
                                       .values(processing_started_at=None))
            await self.session.commit()
            raise

        if reply is not None and send:
            try:
                reply = await deliver_message(self.session, self.wa, reply, ctx.conversation, ctx.customer)
            except WhatsAppSendError:
                logger.warning("AI reply %s could not be sent (recorded for monitoring)", reply.id)
            await self.session.refresh(reply)
            decision.reply_send_status = reply.send_status.value
        return decision

    # ------------------------------------------------------------------ claim
    async def _claim(self, message_id: uuid.UUID) -> bool:
        """Atomic 'I take this message'. Only one caller can win the UPDATE."""
        now = datetime.now(UTC)
        claimed = await self.session.scalar(
            update(Message)
            .where(Message.id == message_id, Message.processed_at.is_(None),
                   or_(Message.processing_started_at.is_(None),
                       Message.processing_started_at < now - CLAIM_TIMEOUT))
            .values(processing_started_at=now)
            .returning(Message.id)
        )
        await self.session.commit()
        return claimed is not None

    # ------------------------------------------------------------------ decide
    async def _decide(self, ctx: AgentContext) -> Plan:
        plan = Plan()
        text = (ctx.message.body or "").strip()
        keyword = text.upper()
        biz = ctx.business_info.get("name", "our team")

        if ctx.conversation.mode == ConversationMode.HUMAN:
            plan.skip_reason = "human_mode"          # a person is handling it: AI stays silent
            return plan

        if keyword in OPT_OUT_WORDS:
            plan.intent = "opt_out"
            plan.reply = (f"You have been unsubscribed from {biz} marketing messages. "
                          "You can still message us any time. Reply START to subscribe again.")
            return plan
        if keyword in OPT_IN_WORDS:
            plan.intent = "opt_in"
            plan.reply = f"Thanks! You're subscribed to updates from {biz}. Reply STOP any time to opt out."
            return plan

        if self._asks_for_human(text, ctx.ai_config.get("handoff_keywords", [])):
            plan.intent = "human_request"
            plan.handoff_reason = "Customer asked to talk to a person"
            plan.handoff_by = ActorType.CUSTOMER
            plan.reply = self._handoff_reply(ctx)
            return plan

        llm = self.llm_factory(ctx.ai_config.get("model"))
        try:
            support = await SupportAgent(llm).run(ctx)
            plan.modules.append("support")
        except LLMError as exc:
            return self._fallback(ctx, plan, exc)

        plan.intent, plan.confidence = support.intent, support.confidence
        plan.used_faq_ids = support.used_faq_ids

        if support.intent in ("human_request", "complaint") or support.needs_human:
            plan.handoff_reason = f"{support.intent.replace('_', ' ')}: {support.reasoning}"
            plan.reply = self._handoff_reply(ctx)
            return plan

        if support.intent in SALES_INTENTS:
            try:
                sales = await SalesAgent(llm).run(ctx, support.intent)
                plan.modules.append("sales")
                plan.reply, plan.facts = sales.reply, sales.facts
            except LLMError as exc:
                plan.llm_error, plan.fallback_used = exc, True
                plan.reply = support.answer           # support's answer is still usable
        elif support.confidence < float(ctx.ai_config.get("faq_confidence_threshold", 0.6)) \
                and support.intent != "greeting":
            plan.handoff_reason = f"Low confidence ({support.confidence:.2f}): {support.reasoning}"
            plan.reply = self._handoff_reply(ctx)
            return plan
        else:
            plan.reply = support.answer

        self._apply_guardrails(ctx, plan)
        if not plan.reply:
            plan.handoff_reason = "AI produced an empty reply"
            plan.reply = self._handoff_reply(ctx)
        return plan

    def _fallback(self, ctx: AgentContext, plan: Plan, exc: LLMError) -> Plan:
        """Gemini is down / not configured: degrade gracefully instead of going silent."""
        logger.warning("LLM unavailable (%s): using fallback", exc.error_type)
        plan.llm_error, plan.fallback_used = exc, True
        plan.modules.append("fallback")
        if ctx.faqs:                                  # best full-text match from our own FAQs
            plan.intent, plan.reply = "faq", ctx.faqs[0].answer
            plan.used_faq_ids = [ctx.faqs[0].id]
        else:
            plan.intent = "other"
            plan.handoff_reason = "AI unavailable and no matching FAQ"
            plan.reply = self._handoff_reply(ctx)
        return plan

    def _apply_guardrails(self, ctx: AgentContext, plan: Plan) -> None:
        plan.reply = clean_reply(plan.reply or "")
        if ctx.ai_config.get("never_invent_prices", True):
            bad = find_unapproved_amounts(plan.reply, allowed_amounts(ctx.services, ctx.all_faq_answers))
            if bad:
                plan.guardrails.extend(f"unapproved_amount: {b}" for b in bad)
                plan.reply = SAFE_PRICE_REPLY
                logger.warning("Guardrail replaced a reply containing %s", bad)

    @staticmethod
    def _asks_for_human(text: str, keywords: list[str]) -> bool:
        low = text.lower()
        return any(re.search(rf"\b{re.escape(k.lower())}\b", low) for k in keywords if k.strip())

    @staticmethod
    def _handoff_reply(ctx: AgentContext) -> str:
        b = ctx.business_info
        hours = f" (our hours: {b['business_hours']})" if b.get("business_hours") else ""
        return (f"Thanks for your patience! I'm connecting you with a member of the "
                f"{b.get('name', 'our')} team. They'll reply here as soon as possible{hours}.")

    # ------------------------------------------------------------------ apply
    async def _apply(self, ctx: AgentContext, plan: Plan) -> tuple[AgentDecision, Message | None]:
        s, msg, conv = self.session, ctx.message, ctx.conversation
        now = datetime.now(UTC)
        decision = AgentDecision(
            message_id=msg.id, conversation_id=conv.id, customer_wa_id=ctx.customer.wa_id,
            action="skipped" if plan.skip_reason else ("handoff" if plan.handoff_reason else "replied"),
            skip_reason=plan.skip_reason, intent=plan.intent, confidence=plan.confidence,
            handoff_reason=plan.handoff_reason, fallback_used=plan.fallback_used,
            guardrails=plan.guardrails, modules=plan.modules,
            assigned_agent_id=conv.assigned_agent_id,
        )

        # 1) Lead capture + qualification (sales conversations only)
        if plan.facts is not None or (plan.intent in SALES_INTENTS):
            decision.lead = await self._update_lead(ctx, plan)
            decision.notify_sales = decision.lead.became_hot

        # 2) Handoff: pause the AI for this conversation and open a request
        if plan.handoff_reason:
            conv.mode = ConversationMode.HUMAN
            active = await s.scalar(select(HandoffRequest).where(
                HandoffRequest.conversation_id == conv.id,
                HandoffRequest.status.in_((HandoffStatus.PENDING, HandoffStatus.ACCEPTED))))
            if active is None:
                active = HandoffRequest(conversation_id=conv.id, requested_by=plan.handoff_by,
                                        reason=plan.handoff_reason[:1000])
                s.add(active)
                await s.flush()
            decision.handoff_request_id = active.id

        # 3) The reply (saved as 'pending'; sent after commit)
        reply = None
        if plan.reply:
            reply = new_outbound_message(conv, plan.reply, sender_type=SenderType.AI, reply_to=msg,
                                         intent=plan.intent,
                                         ai_metadata={"model": self._model_name(ctx), "modules": plan.modules,
                                                      "confidence": plan.confidence,
                                                      "guardrails": plan.guardrails,
                                                      "fallback": plan.fallback_used})
            s.add(reply)
            conv.last_message_at = now
            await s.flush()
            decision.reply_message_id, decision.reply_text = reply.id, reply.body
            decision.reply_send_status = reply.send_status.value

        # 4) Gemini problems go to the monitoring page. Grouped: 100 messages during one outage
        #    = ONE row "x100", one alert. Not retried: the fallback already answered the customer.
        if plan.llm_error:
            await record_error(s, source="gemini", operation="gemini", node="orchestrator",
                               error_type=plan.llm_error.error_type, risk=RiskLevel.SAFE,
                               message=str(plan.llm_error), retryable=False,
                               payload={"last_message_id": str(msg.id), "fallback_used": True})

        # 5) Mark the inbound message done + remember the decision (for idempotent retries)
        msg.intent = plan.intent
        msg.processed_at = now
        msg.ai_metadata = {"decision": decision.model_dump(mode="json"),
                           "used_faq_ids": plan.used_faq_ids}
        await s.commit()          # everything above is saved together, or nothing is
        return decision, reply

    async def _update_lead(self, ctx: AgentContext, plan: Plan) -> LeadSummary:
        s, customer = self.session, ctx.customer
        lead = ctx.lead
        created = lead is None
        if created:
            lead = Lead(customer_id=customer.id, conversation_id=ctx.conversation.id)
            s.add(lead)
            await s.flush()
            add_lead_activity(s, lead, activity_type="created", actor_type=ActorType.AI,
                              description="Lead created from WhatsApp conversation")
        old_temp, old_score = (None, None) if created else (lead.temperature, lead.score)

        if plan.facts:
            self._merge_facts(lead, customer, plan.facts)

        q = qualify(lead, plan.intent, ctx.services, ctx.lead_rules)
        lead.score, lead.temperature, lead.qualification_reason = q.score, q.temperature, q.reason
        lead.last_qualified_at = datetime.now(UTC)
        lead.conversation_id = ctx.conversation.id
        if created or q.temperature != old_temp or q.score != old_score:
            add_lead_activity(s, lead, activity_type="qualified", actor_type=ActorType.AI,
                              description=q.reason,
                              details={"score": q.score, "temperature": q.temperature.value,
                                       "previous_score": old_score,
                                       "previous_temperature": old_temp.value if old_temp else None})
        await s.flush()
        became_hot = q.temperature == LeadTemperature.HOT and old_temp != LeadTemperature.HOT
        return LeadSummary(id=lead.id, temperature=q.temperature.value, score=q.score,
                           reason=q.reason, became_hot=became_hot)

    @staticmethod
    def _merge_facts(lead: Lead, customer: Customer, f: LeadFacts) -> None:
        """Only ADD knowledge: a missing value never erases something we already know."""
        if f.customer_name:
            customer.name = f.customer_name.strip()[:120]
        if f.email and _EMAIL.match(f.email.strip()):
            customer.email = f.email.strip().lower()[:255]
        if f.company:
            customer.company = f.company.strip()[:160]
        if f.requirements:
            lead.requirements = f.requirements.strip()
        if f.interested_services:
            lead.interested_services = sorted(set(lead.interested_services or []) | set(f.interested_services))
        if f.budget_text:
            lead.budget_text = f.budget_text.strip()[:120]
        if f.budget_amount is not None:
            lead.estimated_budget = to_decimal(f.budget_amount)
        if f.budget_currency and re.fullmatch(r"[A-Za-z]{3}", f.budget_currency.strip()):
            lead.budget_currency = f.budget_currency.strip().upper()
        if f.timeline:
            lead.timeline = f.timeline.strip()[:120]
        if f.timeline_days is not None:
            lead.timeline_days = f.timeline_days
        if f.is_decision_maker is not None:
            lead.is_decision_maker = f.is_decision_maker
        if f.buying_signals:
            merged = list(dict.fromkeys((lead.buying_signals or []) + [b.strip()[:100] for b in f.buying_signals]))
            lead.buying_signals = merged[:10]

    def _model_name(self, ctx: AgentContext) -> str | None:
        return ctx.ai_config.get("model")

    # ------------------------------------------------------------------ replays
    async def _already_processed(self, msg: Message) -> AgentDecision:
        """A retry (e.g. n8n timeout): return the stored decision, never answer twice."""
        stored = (msg.ai_metadata or {}).get("decision")
        reply = await self.session.scalar(select(Message).where(
            and_(Message.reply_to_message_id == msg.id, Message.direction == MessageDirection.OUTBOUND)))
        if stored:
            decision = AgentDecision.model_validate(stored)
        else:
            decision = AgentDecision(message_id=msg.id, conversation_id=msg.conversation_id,
                                     customer_wa_id="", action="skipped", skip_reason="already_processed")
        if reply is not None:
            decision.reply_message_id, decision.reply_send_status = reply.id, reply.send_status.value
        decision.already_processed = True
        return decision

    async def _skipped(self, msg: Message, reason: str) -> AgentDecision:
        return AgentDecision(message_id=msg.id, conversation_id=msg.conversation_id,
                             customer_wa_id="", action="skipped", skip_reason=reason)
