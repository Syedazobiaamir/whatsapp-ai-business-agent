"""Stage 15: analytics endpoint."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import AppSetting, Conversation, Customer, HandoffRequest, Lead, Message
from app.models.enums import (
    ActorType, AgentRole, LeadStatus, LeadTemperature, MessageDirection, SendStatus, SenderType,
)

TZ = "Asia/Karachi"            # UTC+5, no daylight saving


def karachi_midnight_utc() -> datetime:
    """Today's 00:00 in Karachi, expressed in UTC."""
    try:
        now_k = datetime.now(ZoneInfo(TZ))
        return now_k.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    except Exception:          # Windows without tzdata: Karachi is a fixed UTC+5
        now_k = datetime.now(UTC) + timedelta(hours=5)
        return now_k.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5)


@pytest.fixture
async def data(session):
    session.add(AppSetting(key="business_info", value={"name": "Test", "description": "", "timezone": TZ,
                                                       "business_hours": "9-5"}))
    mid = karachi_midnight_utc()
    c1 = Customer(wa_id="923000000001", name="A", language="ur", last_seen_at=mid + timedelta(minutes=30))
    c2 = Customer(wa_id="923000000002", name="B", language="en", last_seen_at=mid - timedelta(minutes=30))
    session.add_all([c1, c2])
    await session.flush()
    v1 = Conversation(customer_id=c1.id)
    v2 = Conversation(customer_id=c2.id)
    session.add_all([v1, v2])
    await session.flush()

    def msg(conv, sender, at, intent=None, meta=None):
        inbound = sender == SenderType.CUSTOMER
        return Message(conversation_id=conv.id, sender_type=sender, created_at=at, intent=intent, ai_metadata=meta,
                       direction=MessageDirection.INBOUND if inbound else MessageDirection.OUTBOUND,
                       send_status=SendStatus.RECEIVED if inbound else SendStatus.SENT, body="x")
    # Karachi "today" (00:30): customer -> AI after 40 s.   Karachi "yesterday" (23:30): customer -> human after 300 s
    for m in [
        msg(v1, SenderType.CUSTOMER, mid + timedelta(minutes=30), intent="pricing"),
        msg(v1, SenderType.AI, mid + timedelta(minutes=30, seconds=40), meta={"fallback": True, "guardrails": []}),
        msg(v2, SenderType.CUSTOMER, mid - timedelta(minutes=30), intent="pricing"),
        msg(v2, SenderType.HUMAN, mid - timedelta(minutes=25)),
        msg(v2, SenderType.CUSTOMER, mid - timedelta(minutes=20), intent="complaint"),
        msg(v2, SenderType.AI, mid - timedelta(minutes=19), meta={"guardrails": ["unapproved_amount: $99"]}),
    ]:
        session.add(m)
        await session.flush()                          # keep insertion order = seq order
    session.add_all([
        Lead(customer_id=c1.id, temperature=LeadTemperature.HOT, status=LeadStatus.WON, created_at=mid + timedelta(hours=1)),
        Lead(customer_id=c2.id, temperature=LeadTemperature.HOT, status=LeadStatus.NEW, created_at=mid - timedelta(hours=1)),
        HandoffRequest(conversation_id=v2.id, requested_by=ActorType.AI, reason="complaint",
                       created_at=mid - timedelta(minutes=19)),
    ])
    await session.commit()


async def get(client, make_agent, **params):
    _, h = await make_agent(AgentRole.VIEWER)
    r = await client.get("/api/v1/stats/analytics", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_days_are_counted_in_business_timezone(client, make_agent, data):
    a = await get(client, make_agent, days=7)
    assert a["timezone"] == TZ and len(a["daily"]) == 7
    today, yesterday = a["daily"][-1], a["daily"][-2]
    # 00:30 Karachi = 19:30 UTC the day before: it must still count as Karachi "today".
    assert (today["inbound"], today["ai_replies"], today["new_leads_hot"]) == (1, 1, 1)
    assert (yesterday["inbound"], yesterday["human_replies"], yesterday["handoffs"]) == (2, 1, 1)


async def test_totals_and_rates(client, make_agent, data):
    t = (await get(client, make_agent, days=7))["totals"]
    assert (t["inbound"], t["ai_replies"], t["human_replies"]) == (3, 2, 1)
    assert t["ai_share"] == pytest.approx(2 / 3)
    assert (t["conversations_started"], t["handoffs"], t["handoff_rate"]) == (2, 1, 0.5)
    assert (t["fallback_replies"], t["guardrail_blocks"], t["new_leads"]) == (1, 1, 2)


async def test_response_time_median(client, make_agent, data):
    rt = (await get(client, make_agent, days=7))["response_time"]
    assert rt["samples"] == 3                                  # 40 s, 300 s, 60 s
    assert rt["median_seconds"] == pytest.approx(60)


async def test_intents_languages_win_rate(client, make_agent, data):
    a = await get(client, make_agent, days=7)
    assert a["intents"][0] == {"name": "pricing", "count": 2}
    assert {r["name"]: r["count"] for r in a["languages"]} == {"ur": 1, "en": 1}
    hot = next(r for r in a["win_rate_by_temperature"] if r["name"] == "hot")
    assert (hot["won"], hot["total"]) == (1, 2)


async def test_empty_database_gives_zeros_not_errors(client, make_agent):
    a = await get(client, make_agent, days=30)
    assert len(a["daily"]) == 30 and all(d["inbound"] == 0 for d in a["daily"])
    assert a["totals"]["ai_share"] is None and a["response_time"]["median_seconds"] is None


async def test_unknown_timezone_falls_back_to_utc(client, make_agent, session):
    session.add(AppSetting(key="business_info", value={"name": "T", "description": "", "timezone": "Mars/Base",
                                                       "business_hours": "x"}))
    await session.commit()
    assert (await get(client, make_agent, days=3))["timezone"] == "UTC"


@pytest.mark.parametrize("days", [0, 366])
async def test_days_limits(client, make_agent, days):
    _, h = await make_agent(AgentRole.VIEWER)
    assert (await client.get("/api/v1/stats/analytics", params={"days": days}, headers=h)).status_code == 422


async def test_requires_login(client):
    assert (await client.get("/api/v1/stats/analytics")).status_code == 401
