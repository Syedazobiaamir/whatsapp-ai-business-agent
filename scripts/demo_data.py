"""Load realistic DEMO data for screenshots and the Loom video (fictional people, mock WhatsApp).

  backend\\.venv\\Scripts\\python scripts\\demo_data.py           # (re)create the demo data
  backend\\.venv\\Scripts\\python scripts\\demo_data.py --remove  # delete it again

Every demo customer has a WhatsApp id starting with 9230088 so it can be removed cleanly.
Nothing is sent to WhatsApp; this writes to the database only.
"""
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.ai.lead_qualification import qualify  # noqa: E402
from app.models import (  # noqa: E402
    AppSetting, Conversation, Customer, FollowUpTask, HandoffRequest, Lead, LeadActivity, Message, Service,
)
from app.models.enums import (  # noqa: E402
    ActorType, ConversationMode, FollowUpStatus, HandoffStatus, LeadStatus, LeadTemperature,
    MessageDirection, MessageType, SendStatus, SenderType,
)

PREFIX = "9230088"
NOW = datetime.now(UTC)

# (wa_id suffix, name, company, email, opt_in, mode, minutes_ago, conversation, lead or None)
# conversation: list of (sender, text, intent)   lead: dict of fields
# NOTE: temperature/score/reason in these dicts are ignored - they are recomputed by the real
# qualification rules (app.ai.lead_qualification.qualify) so the demo shows true system output.
PEOPLE = [
    ("0001", "Hamza Siddiqui", "Siddiqui Fabrics", "hamza@siddiquifabrics.pk", True, ConversationMode.AI, 35, [
        ("customer", "Assalam o alaikum! We sell fabric in Faisalabad and want to sell online.", None),
        ("ai", "Walaikum assalam Hamza! 👋 An online store is a great fit. Our E-commerce Store service "
               "(USD 900-3,000) includes payments, product setup and order emails. What budget do you have in mind?",
         "service_inquiry"),
        ("customer", "Around 2000 dollars. I am the owner so I decide. Can we start in 2 weeks?", None),
        ("ai", "Perfect, USD 2,000 fits an E-commerce Store well, and starting in 2 weeks is possible. "
               "I'll ask our team to prepare a detailed quote for you today.", "buying_intent"),
    ], dict(temperature=LeadTemperature.HOT, score=90, status=LeadStatus.QUALIFIED,
            requirements="Online fabric store with card payments and delivery tracking",
            services=["E-commerce Store"], budget_text="around 2000 dollars", budget=Decimal(2000),
            timeline="in 2 weeks", timeline_days=14, decision_maker=True,
            signals=["asked to start in 2 weeks", "shared budget"],
            reason="HOT (90/100): clear requirements; interested in E-commerce Store; budget stated (around 2000 dollars); "
                   "budget fits the service price range; wants to start within 14 days; is the decision maker; "
                   "buying signals: asked to start in 2 weeks; shared budget; intent: buying intent")),
    ("0002", "Ayesha Khan", "Bloom Bakery", None, True, ConversationMode.AI, 26 * 60, [
        ("customer", "Hi, how much does a website cost for a small bakery?", None),
        ("ai", "Hi Ayesha! A business website usually costs USD 400-1,200, depending on pages and features. "
               "When would you like it to be ready?", "pricing"),
        ("customer", "Maybe in 2-3 months, I need to discuss with my partner.", None),
        ("ai", "That works! I've noted that. Would a menu page and online orders be important for you?", "service_inquiry"),
    ], dict(temperature=LeadTemperature.WARM, score=55, status=LeadStatus.NEW,
            requirements="Website for a small bakery", services=["Business Website"], budget_text=None, budget=None,
            timeline="2-3 months", timeline_days=75, decision_maker=False, signals=[],
            reason="WARM (55/100): clear requirements; interested in Business Website; timeline within 3 months; "
                   "intent: pricing")),
    ("0003", "Usman Tariq", None, None, False, ConversationMode.AI, 3 * 60, [
        ("customer", "What are your working hours?", None),
        ("ai", "We are available Monday to Friday, 10:00 to 19:00 (Pakistan time). How can I help you today?", "faq"),
    ], dict(temperature=LeadTemperature.COLD, score=5, status=LeadStatus.NEW, requirements=None, services=[],
            budget_text=None, budget=None, timeline=None, timeline_days=None, decision_maker=None, signals=[],
            reason="COLD (5/100): intent: service inquiry")),
    ("0004", "Sana Malik", "Malik Clinics", None, True, ConversationMode.HUMAN, 8, [
        ("customer", "I paid the advance last week but nobody contacted me. Very disappointed.", None),
        ("ai", "Thanks for your patience! I'm connecting you with a member of the NovaByte Digital Studio team. "
               "They'll reply here as soon as possible (our hours: Monday-Friday, 10:00-19:00).", "complaint"),
    ], None),
]


async def remove(s) -> int:
    ids = (await s.scalars(select(Customer.id).where(Customer.wa_id.like(f"{PREFIX}%")))).all()
    await s.execute(delete(Customer).where(Customer.id.in_(ids)))     # cascades to everything else
    return len(ids)


async def create(s) -> None:
    services = (await s.scalars(select(Service).where(Service.is_active.is_(True)))).all()
    row = await s.get(AppSetting, "lead_rules")
    rules = row.value if row else {"hot_min_score": 70, "warm_min_score": 40}
    for suffix, name, company, email, opt_in, mode, minutes_ago, chat, lead_data in PEOPLE:
        start = NOW - timedelta(minutes=minutes_ago)
        c = Customer(wa_id=PREFIX + "0" + suffix, name=name, company=company, email=email,
                     marketing_opt_in=opt_in, last_seen_at=start)
        s.add(c)
        await s.flush()
        conv = Conversation(customer_id=c.id, mode=mode)
        s.add(conv)
        await s.flush()
        t = start
        for i, (sender, text, intent) in enumerate(chat):
            t = start + timedelta(seconds=40 * i)
            inbound = sender == "customer"
            s.add(Message(conversation_id=conv.id, created_at=t, body=text, intent=intent,
                          direction=MessageDirection.INBOUND if inbound else MessageDirection.OUTBOUND,
                          sender_type=SenderType.CUSTOMER if inbound else SenderType.AI,
                          wa_message_id=f"wamid.DEMO.{suffix}.{i}" if inbound else None,
                          send_status=SendStatus.RECEIVED if inbound else SendStatus.MOCKED,
                          ai_metadata=None if inbound else {"modules": ["support", "sales"] if intent in
                                                            ("buying_intent", "service_inquiry", "pricing") else ["support"],
                                                            "confidence": 0.92, "guardrails": [], "fallback": False}))
            if inbound:
                conv.last_customer_message_at = t
        conv.last_message_at = t
        await s.flush()
        if lead_data:
            d = lead_data
            lead = Lead(customer_id=c.id, conversation_id=conv.id, status=d["status"], requirements=d["requirements"],
                        interested_services=d["services"], budget_text=d["budget_text"], estimated_budget=d["budget"],
                        budget_currency="USD" if d["budget"] else None, timeline=d["timeline"],
                        timeline_days=d["timeline_days"], is_decision_maker=d["decision_maker"],
                        buying_signals=d["signals"], last_qualified_at=t)
            # Score with the REAL qualification rules (not hand-written numbers)
            last_intent = next((i for _, _, i in reversed(chat) if i), None)
            q = qualify(lead, last_intent, services, rules)
            lead.score, lead.temperature, lead.qualification_reason = q.score, q.temperature, q.reason
            d = {**d, "temperature": q.temperature, "score": q.score, "reason": q.reason}
            s.add(lead)
            await s.flush()
            s.add_all([
                LeadActivity(lead_id=lead.id, activity_type="created", actor_type=ActorType.AI,
                             description="Lead created from WhatsApp conversation", created_at=start),
                LeadActivity(lead_id=lead.id, activity_type="qualified", actor_type=ActorType.AI,
                             description=d["reason"], details={"score": d["score"]}, created_at=t),
            ])
            if d["temperature"] == LeadTemperature.WARM:      # quiet for 26h -> follow-up waiting for approval
                s.add(FollowUpTask(lead_id=lead.id, customer_id=c.id, scheduled_at=NOW, template_name="hello_world",
                                   status=FollowUpStatus.PENDING_APPROVAL, requires_approval=True,
                                   idempotency_key=f"lead:{lead.id}:followup:1"))
                s.add(LeadActivity(lead_id=lead.id, activity_type="follow_up_planned", actor_type=ActorType.N8N,
                                   description="Follow-up #1 planned (template hello_world)"))
        if mode == ConversationMode.HUMAN:
            s.add(HandoffRequest(conversation_id=conv.id, requested_by=ActorType.AI, status=HandoffStatus.PENDING,
                                 reason="complaint: customer says nobody contacted them after paying the advance"))


async def main():
    async with SessionLocal() as s:
        async with s.begin():
            removed = await remove(s)
            if "--remove" not in sys.argv:
                await create(s)
    await engine.dispose()
    print(f"removed {removed} demo customers" + ("" if "--remove" in sys.argv else f"; created {len(PEOPLE)} demo customers"))

asyncio.run(main())
