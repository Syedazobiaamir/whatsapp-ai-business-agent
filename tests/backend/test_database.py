"""Stage 3 tests: schema, constraints and database rules."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import (
    BusinessFAQ,
    Conversation,
    Customer,
    FollowUpTask,
    HandoffRequest,
    Lead,
    Message,
)
from app.models.enums import (
    ActorType,
    ConversationMode,
    ConversationStatus,
    LeadTemperature,
    MessageDirection,
    SenderType,
)

EXPECTED_TABLES = {
    "customers", "conversations", "messages", "leads", "lead_activities",
    "business_faqs", "human_agents", "handoff_requests", "follow_up_tasks",
    "automation_errors", "audit_logs", "services", "app_settings",
}


async def make_conversation(session: AsyncSession, wa_id: str = "923001234567") -> Conversation:
    customer = Customer(wa_id=wa_id, name="Test Customer")
    session.add(customer)
    await session.flush()                      # sends INSERT, gives us customer.id
    conv = Conversation(customer_id=customer.id)
    session.add(conv)
    await session.flush()
    return conv


async def test_all_tables_exist(session: AsyncSession):
    conn = await session.connection()
    names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert EXPECTED_TABLES <= names


async def test_defaults_and_timestamps(session: AsyncSession):
    conv = await make_conversation(session)
    await session.commit()
    await session.refresh(conv)
    assert conv.status == ConversationStatus.OPEN
    assert conv.mode == ConversationMode.AI          # AI answers by default
    assert conv.created_at is not None and conv.updated_at is not None


async def test_duplicate_wa_message_id_is_rejected(session: AsyncSession):
    """Meta may deliver the same webhook twice: only ONE row may exist."""
    conv = await make_conversation(session)
    for _ in range(2):
        session.add(Message(
            conversation_id=conv.id, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, wa_message_id="wamid.SAME", body="Hi",
        ))
    with pytest.raises(IntegrityError, match="uq_messages_wa_message_id"):
        await session.flush()


async def test_outbound_messages_without_wa_id_are_allowed(session: AsyncSession):
    """NULL is not a duplicate: many unsent outbound messages can exist."""
    conv = await make_conversation(session)
    for i in range(2):
        session.add(Message(
            conversation_id=conv.id, direction=MessageDirection.OUTBOUND,
            sender_type=SenderType.AI, body=f"reply {i}",
        ))
    await session.flush()


async def test_only_one_open_conversation_per_customer(session: AsyncSession):
    conv = await make_conversation(session)
    session.add(Conversation(customer_id=conv.customer_id))
    with pytest.raises(IntegrityError, match="uq_conversations_one_open_per_customer"):
        await session.flush()


async def test_closed_conversations_do_not_block_a_new_one(session: AsyncSession):
    conv = await make_conversation(session)
    conv.status = ConversationStatus.CLOSED
    await session.flush()
    session.add(Conversation(customer_id=conv.customer_id))
    await session.flush()                     # no error


async def test_lead_score_must_be_0_to_100(session: AsyncSession):
    customer = Customer(wa_id="923000000001")
    session.add(customer)
    await session.flush()
    session.add(Lead(customer_id=customer.id, score=150))
    with pytest.raises(IntegrityError, match="score_range"):
        await session.flush()


async def test_invalid_enum_value_is_rejected_by_database(session: AsyncSession):
    """Even raw SQL cannot store a temperature other than hot/warm/cold."""
    customer = Customer(wa_id="923000000002")
    session.add(customer)
    await session.flush()
    with pytest.raises(IntegrityError, match="lead_temperature"):
        await session.execute(text(
            "INSERT INTO leads (customer_id, temperature, status) "
            "VALUES (:cid, 'boiling', 'new')"
        ), {"cid": customer.id})


async def test_deleting_customer_cascades(session: AsyncSession):
    conv = await make_conversation(session)
    session.add(Message(conversation_id=conv.id, direction=MessageDirection.INBOUND,
                        sender_type=SenderType.CUSTOMER, body="Hello"))
    session.add(Lead(customer_id=conv.customer_id, temperature=LeadTemperature.HOT, score=80))
    await session.commit()

    await session.execute(text("DELETE FROM customers"))
    await session.commit()
    for model in (Conversation, Message, Lead):
        assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_only_one_active_handoff_per_conversation(session: AsyncSession):
    conv = await make_conversation(session)
    for _ in range(2):
        session.add(HandoffRequest(conversation_id=conv.id,
                                   requested_by=ActorType.CUSTOMER, reason="wants human"))
    with pytest.raises(IntegrityError, match="uq_handoff_requests_one_active"):
        await session.flush()


async def test_follow_up_idempotency_key_prevents_duplicates(session: AsyncSession):
    customer = Customer(wa_id="923000000003")
    session.add(customer)
    await session.flush()
    lead = Lead(customer_id=customer.id)
    session.add(lead)
    await session.flush()
    when = datetime.now(UTC) + timedelta(days=1)
    for _ in range(2):
        session.add(FollowUpTask(lead_id=lead.id, customer_id=customer.id, scheduled_at=when,
                                 template_name="lead_follow_up",
                                 idempotency_key=f"lead:{lead.id}:followup:1"))
    with pytest.raises(IntegrityError, match="uq_follow_up_tasks_idempotency_key"):
        await session.flush()


async def test_faq_full_text_search(session: AsyncSession):
    session.add_all([
        BusinessFAQ(question="How much does an online store cost?",
                    answer="Between 900 and 3000 USD.", keywords="shop, ecommerce, price"),
        BusinessFAQ(question="What are your hours?", answer="10 to 7.", keywords="timing"),
    ])
    await session.flush()
    query = func.websearch_to_tsquery("english", "price of an online shop")
    rows = (await session.scalars(
        select(BusinessFAQ.question).where(BusinessFAQ.search_vector.op("@@")(query))
    )).all()
    assert rows == ["How much does an online store cost?"]


def test_password_hashing():
    hashed = hash_password("S3cure-Passw0rd")
    assert hashed != "S3cure-Passw0rd"            # never stored as plain text
    assert verify_password("S3cure-Passw0rd", hashed)
    assert not verify_password("wrong", hashed)
