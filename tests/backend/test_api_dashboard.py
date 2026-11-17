"""Stage 4 tests: dashboard APIs (leads, conversations, knowledge base, settings, stats)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Conversation, Customer, HandoffRequest, Lead, Message
from app.models.enums import (
    AgentRole,
    ConversationMode,
    HandoffStatus,
    LeadStatus,
    LeadTemperature,
    MessageDirection,
    SenderType,
)


async def make_lead(session: AsyncSession, name: str, wa_id: str, temp: LeadTemperature,
                    score: int, requirements: str = "") -> tuple[Customer, Conversation, Lead]:
    customer = Customer(wa_id=wa_id, name=name)
    session.add(customer)
    await session.flush()
    now = datetime.now(UTC)
    conv = Conversation(customer_id=customer.id, last_message_at=now,
                        last_customer_message_at=now - timedelta(hours=1))
    session.add(conv)
    await session.flush()
    session.add_all([
        Message(conversation_id=conv.id, direction=MessageDirection.INBOUND,
                sender_type=SenderType.CUSTOMER, body=f"Hi, I need {requirements}",
                wa_message_id=f"wamid.{wa_id}.1"),
        Message(conversation_id=conv.id, direction=MessageDirection.OUTBOUND,
                sender_type=SenderType.AI, body="Sure! What is your budget?"),
    ])
    lead = Lead(customer_id=customer.id, conversation_id=conv.id, temperature=temp, score=score,
                requirements=requirements, qualification_reason="test data")
    session.add(lead)
    await session.commit()
    return customer, conv, lead


@pytest.fixture
async def sample(session):
    return {
        "ali": await make_lead(session, "Ali Khan", "923001111111", LeadTemperature.HOT, 85, "an online store"),
        "sara": await make_lead(session, "Sara Ahmed", "923002222222", LeadTemperature.WARM, 55, "a website"),
        "omar": await make_lead(session, "Omar Malik", "923003333333", LeadTemperature.COLD, 10, "just browsing"),
    }


async def test_leads_filter_search_sort_paginate(client, make_agent, sample):
    _, h = await make_agent(AgentRole.VIEWER)
    r = await client.get("/api/v1/leads", params={"temperature": "hot"}, headers=h)
    assert [x["customer"]["name"] for x in r.json()["items"]] == ["Ali Khan"]

    r = await client.get("/api/v1/leads", params={"q": "website"}, headers=h)    # searches requirements
    assert [x["customer"]["name"] for x in r.json()["items"]] == ["Sara Ahmed"]

    r = await client.get("/api/v1/leads", params={"q": "92300333"}, headers=h)   # searches phone
    assert r.json()["total"] == 1

    r = await client.get("/api/v1/leads", params={"sort": "score", "size": 2, "page": 1}, headers=h)
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert [x["score"] for x in body["items"]] == [85, 55]

    r = await client.get("/api/v1/leads", params={"size": 500}, headers=h)
    assert r.status_code == 422                   # page size is capped at 100


async def test_update_lead_records_activity_and_audit(client, make_agent, sample, session):
    agent, h = await make_agent(AgentRole.SALES_AGENT)
    lead = sample["sara"][2]
    r = await client.patch(f"/api/v1/leads/{lead.id}", headers=h, json={
        "status": "contacted", "assigned_agent_id": str(agent.id), "note": "Called, sending proposal",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "contacted" and data["assigned_agent_id"] == str(agent.id)
    types = {a["activity_type"] for a in data["activities"]}
    assert {"status_changed", "assigned_agent_id_changed", "note"} <= types

    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "lead.updated"))
    assert audit.actor_agent_id == agent.id and audit.changes["status"] == ["new", "contacted"]


async def test_viewer_cannot_update_lead(client, make_agent, sample):
    _, h = await make_agent(AgentRole.VIEWER)
    r = await client.patch(f"/api/v1/leads/{sample['ali'][2].id}", headers=h, json={"status": "won"})
    assert r.status_code == 403


async def test_conversation_list_and_detail(client, make_agent, sample):
    _, h = await make_agent(AgentRole.VIEWER)
    r = await client.get("/api/v1/conversations", headers=h)
    items = r.json()["items"]
    assert len(items) == 3
    assert all(i["last_message_preview"] for i in items)

    conv_id = sample["ali"][1].id
    d = (await client.get(f"/api/v1/conversations/{conv_id}", headers=h)).json()
    assert [m["sender_type"] for m in d["messages"]] == ["customer", "ai"]   # oldest first
    assert d["within_24h_window"] is True
    assert d["mode"] == "ai"


async def test_takeover_pauses_ai_and_resume_restores_it(client, make_agent, sample, session):
    agent, h = await make_agent(AgentRole.SALES_AGENT)
    conv_id = sample["ali"][1].id

    r = await client.post(f"/api/v1/conversations/{conv_id}/takeover", headers=h,
                          json={"reason": "Big deal, handling personally"})
    assert r.status_code == 200
    assert r.json()["mode"] == "human" and r.json()["assigned_agent_id"] == str(agent.id)
    assert r.json()["has_pending_handoff"] is True
    handoff = await session.scalar(select(HandoffRequest).where(HandoffRequest.conversation_id == conv_id))
    assert handoff.status == HandoffStatus.ACCEPTED

    r = await client.post(f"/api/v1/conversations/{conv_id}/resume", headers=h)
    assert r.status_code == 200 and r.json()["mode"] == "ai"
    await session.refresh(handoff)
    assert handoff.status == HandoffStatus.RESOLVED

    again = await client.post(f"/api/v1/conversations/{conv_id}/resume", headers=h)
    assert again.status_code == 409               # already AI


async def test_overview_stats(client, make_agent, sample, session):
    _, h = await make_agent(AgentRole.VIEWER)
    conv = sample["sara"][1]
    conv.mode = ConversationMode.HUMAN
    session.add(HandoffRequest(conversation_id=conv.id, requested_by="customer", reason="wants human"))
    lead = await session.get(Lead, sample["omar"][2].id)
    lead.status = LeadStatus.LOST
    await session.commit()

    s = (await client.get("/api/v1/stats/overview", headers=h)).json()
    assert s["total_customers"] == 3 and s["total_leads"] == 3
    assert (s["hot_leads"], s["warm_leads"], s["cold_leads"]) == (1, 1, 1)
    assert s["active_conversations"] == 3 and s["conversations_in_human_mode"] == 1
    assert s["pending_handoffs"] == 1
    assert s["leads_by_status"] == {"new": 2, "lost": 1}
    assert s["messages_last_24h"] == 6


async def test_faq_crud_and_search(client, make_agent):
    _, h = await make_agent(AgentRole.ADMIN)
    body = {"question": "Do you build online shops?", "answer": "Yes, Shopify and WooCommerce.",
            "category": "services", "keywords": "ecommerce, store"}
    created = await client.post("/api/v1/faqs", json=body, headers=h)
    assert created.status_code == 201
    faq_id = created.json()["id"]
    assert (await client.post("/api/v1/faqs", json=body, headers=h)).status_code == 409

    found = await client.get("/api/v1/faqs", params={"q": "ecommerce store"}, headers=h)
    assert found.json()["total"] == 1

    upd = await client.patch(f"/api/v1/faqs/{faq_id}", json={"is_active": False}, headers=h)
    assert upd.json()["is_active"] is False
    assert (await client.delete(f"/api/v1/faqs/{faq_id}", headers=h)).status_code == 204
    assert (await client.get("/api/v1/faqs", headers=h)).json()["total"] == 0


async def test_service_price_range_validated(client, make_agent):
    _, h = await make_agent(AgentRole.ADMIN)
    bad = {"name": "Logo Design", "description": "Brand logo", "price_from": 200, "price_to": 100}
    assert (await client.post("/api/v1/services", json=bad, headers=h)).status_code == 422
    ok = await client.post("/api/v1/services", json=bad | {"price_to": 300}, headers=h)
    assert ok.status_code == 201
    sid = ok.json()["id"]
    # Partial update that would break the range (300 -> 50 while from=200) is rejected:
    r = await client.patch(f"/api/v1/services/{sid}", json={"price_to": 50}, headers=h)
    assert r.status_code == 422


async def test_settings_are_validated_per_key(client, make_agent):
    _, h = await make_agent(AgentRole.ADMIN)
    good = {"model": "gemini-2.5-flash", "temperature": 0.2, "tone": "friendly",
            "max_history_messages": 10, "faq_confidence_threshold": 0.6,
            "handoff_keywords": ["human"], "never_invent_prices": True}
    r = await client.put("/api/v1/settings/ai_config", json=good, headers=h)
    assert r.status_code == 200 and r.json()["value"]["temperature"] == 0.2

    bad = await client.put("/api/v1/settings/ai_config", json=good | {"temperature": 5}, headers=h)
    assert bad.status_code == 422                 # temperature must be 0..1
    unknown = await client.put("/api/v1/settings/hacker_mode", json={}, headers=h)
    assert unknown.status_code == 404


async def test_customer_opt_out_sets_timestamp(client, make_agent, sample):
    _, h = await make_agent(AgentRole.SALES_AGENT)
    cid = sample["ali"][0].id
    await client.patch(f"/api/v1/customers/{cid}", json={"marketing_opt_in": True}, headers=h)
    r = await client.patch(f"/api/v1/customers/{cid}", json={"marketing_opt_in": False}, headers=h)
    assert r.json()["marketing_opt_in"] is False and r.json()["opted_out_at"] is not None


async def test_unknown_ids_return_404(client, make_agent):
    _, h = await make_agent(AgentRole.ADMIN)
    missing = "00000000-0000-0000-0000-000000000000"
    for url in (f"/api/v1/leads/{missing}", f"/api/v1/conversations/{missing}",
                f"/api/v1/customers/{missing}"):
        assert (await client.get(url, headers=h)).status_code == 404
    assert (await client.get("/api/v1/leads/not-a-uuid", headers=h)).status_code == 422
