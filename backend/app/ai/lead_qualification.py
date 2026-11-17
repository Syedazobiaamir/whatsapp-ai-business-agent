"""C. Lead Qualification: Hot / Warm / Cold with a human-readable reason.

Design choice: Gemini EXTRACTS facts ("budget 1500 USD", "start in 2 weeks"), but the SCORE
comes from transparent rules below. Rules are predictable, testable, explainable to a sales
manager, and cannot be talked into "HOT" by a customer typing clever text.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.models import Lead, Service
from app.models.enums import LeadTemperature

SALES_INTENT_POINTS = {"buying_intent": 15, "pricing": 5, "service_inquiry": 5}


@dataclass
class Qualification:
    score: int
    temperature: LeadTemperature
    reason: str
    signals: list[str]


def qualify(lead: Lead, intent: str | None, services: list[Service], rules: dict) -> Qualification:
    points = 0
    signals: list[str] = []

    def add(n: int, why: str) -> None:
        nonlocal points
        points += n
        signals.append(why)

    if lead.requirements:
        add(15, "clear requirements")
    interested = lead.interested_services or []
    if interested:
        add(15, f"interested in {', '.join(interested)}")

    if lead.estimated_budget is not None or lead.budget_text:
        add(15, f"budget stated ({lead.budget_text or lead.estimated_budget})")
        # Budget big enough for the cheapest service they want (same currency only)?
        mins = [s.price_from for s in services
                if s.name in interested and s.price_from is not None
                and (lead.budget_currency or s.currency) == s.currency]
        if lead.estimated_budget is not None and mins and lead.estimated_budget >= min(mins):
            add(10, "budget fits the service price range")

    if lead.timeline_days is not None:
        if lead.timeline_days <= 30:
            add(20, f"wants to start within {lead.timeline_days} days")
        elif lead.timeline_days <= 90:
            add(10, "timeline within 3 months")
    elif lead.timeline:
        add(5, f"timeline mentioned ({lead.timeline})")

    if lead.is_decision_maker:
        add(10, "is the decision maker")

    buying = lead.buying_signals or []
    if buying:
        add(min(15, 5 * len(buying)), "buying signals: " + "; ".join(buying[:3]))

    if intent in SALES_INTENT_POINTS:
        add(SALES_INTENT_POINTS[intent], f"intent: {intent.replace('_', ' ')}")

    score = max(0, min(100, points))
    hot, warm = int(rules.get("hot_min_score", 70)), int(rules.get("warm_min_score", 40))
    temperature = (LeadTemperature.HOT if score >= hot
                   else LeadTemperature.WARM if score >= warm else LeadTemperature.COLD)
    reason = f"{temperature.value.upper()} ({score}/100): " + (
        "; ".join(signals) if signals else "no qualifying information yet")
    return Qualification(score, temperature, reason[:1000], signals)


def to_decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 2)))
