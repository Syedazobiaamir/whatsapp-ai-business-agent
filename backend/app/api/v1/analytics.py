"""Analytics page (Stage 15): trends over the last N days, computed in PostgreSQL.

Days are counted in the BUSINESS timezone (business_info.timezone, e.g. Asia/Karachi):
a message at 23:30 UTC is "tomorrow" in Pakistan, so UTC days would put it on the wrong day.
"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import AnyRole, SessionDep
from app.models import AppSetting

router = APIRouter(tags=["dashboard"])


class DayRow(BaseModel):
    day: date
    inbound: int          # customer messages
    ai_replies: int
    human_replies: int
    new_leads_hot: int
    new_leads_warm: int
    new_leads_cold: int
    handoffs: int


class CountRow(BaseModel):
    name: str
    count: int


class RateRow(BaseModel):
    name: str
    won: int
    total: int


class Totals(BaseModel):
    inbound: int
    ai_replies: int
    human_replies: int
    ai_share: float | None          # AI replies / all replies
    conversations_started: int
    handoffs: int
    handoff_rate: float | None      # handoffs / conversations started
    fallback_replies: int           # answered from FAQ search because Gemini was unavailable
    guardrail_blocks: int           # replies replaced because of an unapproved price
    new_leads: int


class ResponseTime(BaseModel):
    median_seconds: float | None    # customer message -> our next reply
    p90_seconds: float | None
    samples: int


class Analytics(BaseModel):
    days: int
    timezone: str
    daily: list[DayRow]
    totals: Totals
    response_time: ResponseTime
    intents: list[CountRow]
    languages: list[CountRow]
    win_rate_by_temperature: list[RateRow]


DAILY_SQL = """
WITH d AS (
  SELECT generate_series((now() AT TIME ZONE :tz)::date - (:days - 1),
                         (now() AT TIME ZONE :tz)::date, interval '1 day')::date AS day
), m AS (
  SELECT (created_at AT TIME ZONE :tz)::date AS day,
         count(*) FILTER (WHERE direction = 'inbound')   AS inbound,
         count(*) FILTER (WHERE sender_type = 'ai')      AS ai_replies,
         count(*) FILTER (WHERE sender_type = 'human')   AS human_replies
  FROM messages WHERE created_at >= now() - make_interval(days => :days + 1) GROUP BY 1
), l AS (
  SELECT (created_at AT TIME ZONE :tz)::date AS day,
         count(*) FILTER (WHERE temperature = 'hot')  AS hot,
         count(*) FILTER (WHERE temperature = 'warm') AS warm,
         count(*) FILTER (WHERE temperature = 'cold') AS cold
  FROM leads WHERE created_at >= now() - make_interval(days => :days + 1) GROUP BY 1
), h AS (
  SELECT (created_at AT TIME ZONE :tz)::date AS day, count(*) AS handoffs
  FROM handoff_requests WHERE created_at >= now() - make_interval(days => :days + 1) GROUP BY 1
)
SELECT d.day,
       coalesce(m.inbound, 0) AS inbound, coalesce(m.ai_replies, 0) AS ai_replies,
       coalesce(m.human_replies, 0) AS human_replies,
       coalesce(l.hot, 0) AS new_leads_hot, coalesce(l.warm, 0) AS new_leads_warm,
       coalesce(l.cold, 0) AS new_leads_cold, coalesce(h.handoffs, 0) AS handoffs
FROM d LEFT JOIN m USING (day) LEFT JOIN l USING (day) LEFT JOIN h USING (day)
ORDER BY d.day
"""

# For each customer message: how long until the next reply (AI or team) in the same chat.
# Ordered by seq (insertion order). Customer timestamps come from Meta (whole seconds) and
# replies from our server clock, so tiny negative values from rounding are clamped to 0.
RESPONSE_SQL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY secs) AS median,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY secs) AS p90,
       count(*) AS samples
FROM (
  SELECT greatest(0, extract(epoch FROM (o.created_at - i.created_at))) AS secs
  FROM messages i
  JOIN LATERAL (
    SELECT created_at FROM messages o
    WHERE o.conversation_id = i.conversation_id AND o.direction = 'outbound' AND o.seq > i.seq
    ORDER BY o.seq LIMIT 1
  ) o ON true
  WHERE i.direction = 'inbound' AND i.created_at >= ((now() AT TIME ZONE :tz)::date - (:days - 1)) AT TIME ZONE :tz
) s
"""

PERIOD = "created_at >= ((now() AT TIME ZONE :tz)::date - (:days - 1)) AT TIME ZONE :tz"

TOTALS_SQL = f"""
SELECT
  (SELECT count(*) FROM messages WHERE direction = 'inbound' AND {PERIOD}) AS inbound,
  (SELECT count(*) FROM messages WHERE sender_type = 'ai' AND {PERIOD}) AS ai_replies,
  (SELECT count(*) FROM messages WHERE sender_type = 'human' AND {PERIOD}) AS human_replies,
  (SELECT count(*) FROM conversations WHERE {PERIOD}) AS conversations_started,
  (SELECT count(*) FROM handoff_requests WHERE {PERIOD}) AS handoffs,
  (SELECT count(*) FROM messages WHERE sender_type = 'ai' AND ai_metadata->>'fallback' = 'true' AND {PERIOD})
      AS fallback_replies,
  (SELECT count(*) FROM messages WHERE sender_type = 'ai'
      AND jsonb_array_length(coalesce(ai_metadata->'guardrails', '[]'::jsonb)) > 0 AND {PERIOD}) AS guardrail_blocks,
  (SELECT count(*) FROM leads WHERE {PERIOD}) AS new_leads
"""

INTENTS_SQL = f"""
SELECT intent AS name, count(*) AS count FROM messages
WHERE direction = 'inbound' AND intent IS NOT NULL AND {PERIOD}
GROUP BY intent ORDER BY count DESC, intent LIMIT 8
"""

LANGUAGES_SQL = """
SELECT coalesce(language, 'unknown') AS name, count(*) AS count FROM customers
WHERE last_seen_at >= ((now() AT TIME ZONE :tz)::date - (:days - 1)) AT TIME ZONE :tz
GROUP BY 1 ORDER BY count DESC
"""

WIN_RATE_SQL = """
SELECT t.name, coalesce(count(l.id) FILTER (WHERE l.status = 'won'), 0) AS won, count(l.id) AS total
FROM (VALUES ('hot', 1), ('warm', 2), ('cold', 3)) AS t(name, ord)
LEFT JOIN leads l ON l.temperature = t.name
GROUP BY t.name, t.ord ORDER BY t.ord
"""


@router.get("/stats/analytics", response_model=Analytics)
async def analytics(_: AnyRole, session: SessionDep,
                    days: Annotated[int, Query(ge=1, le=365)] = 30):
    info = await session.get(AppSetting, "business_info")
    tz = (info.value.get("timezone") if info else None) or "UTC"
    # Ask PostgreSQL (the one that uses it) whether the zone exists. Python's zoneinfo cannot be
    # used for this: on Windows it has no timezone database and wrongly rejects "Asia/Karachi".
    known = await session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :tz)"),
                                 {"tz": tz})
    if not known:
        tz = "UTC"
    p = {"tz": tz, "days": days}

    daily = [DayRow(**r) for r in (await session.execute(text(DAILY_SQL), p)).mappings()]
    t = (await session.execute(text(TOTALS_SQL), p)).mappings().one()
    replies = t["ai_replies"] + t["human_replies"]
    totals = Totals(**t, ai_share=(t["ai_replies"] / replies) if replies else None,
                    handoff_rate=(t["handoffs"] / t["conversations_started"]) if t["conversations_started"] else None)
    rt = (await session.execute(text(RESPONSE_SQL), p)).mappings().one()
    return Analytics(
        days=days, timezone=tz, daily=daily, totals=totals,
        response_time=ResponseTime(median_seconds=rt["median"], p90_seconds=rt["p90"], samples=rt["samples"]),
        intents=[CountRow(**r) for r in (await session.execute(text(INTENTS_SQL), p)).mappings()],
        languages=[CountRow(**r) for r in (await session.execute(text(LANGUAGES_SQL), p)).mappings()],
        win_rate_by_temperature=[RateRow(**r) for r in (await session.execute(text(WIN_RATE_SQL))).mappings()],
    )
