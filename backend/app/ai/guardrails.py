"""Checks applied to EVERY AI reply before it can reach a customer.

LLMs can "hallucinate" (confidently invent facts). For a business, an invented price or
discount is a real problem, so any amount in a reply must exist in our own data.
"""
import re

from app.models import Service

# "$900", "USD 1,200", "1200 USD", "Rs. 5000", "PKR 50,000", "15%"
_MONEY = re.compile(
    r"(?:(?:usd|us\$|\$|pkr|rs\.?|eur|€|£|gbp)\s?(\d[\d,]*(?:\.\d+)?))"
    r"|(?:(\d[\d,]*(?:\.\d+)?)\s?(?:usd|dollars?|pkr|rupees?|eur|euros?|gbp|k\b))"
    r"|(?:(\d+(?:\.\d+)?)\s?%)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

SAFE_PRICE_REPLY = ("Thanks for asking! Our pricing depends on your exact requirements. "
                    "A team member will confirm an accurate quote for you shortly.")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def allowed_amounts(services: list[Service], faq_answers: list[str]) -> set[float]:
    allowed: set[float] = set()
    for s in services:
        for v in (s.price_from, s.price_to):
            if v is not None:
                allowed.add(float(v))
    for text in faq_answers:
        allowed.update(_num(n) for n in _NUMBER.findall(text))
    return allowed


def find_unapproved_amounts(reply: str, allowed: set[float]) -> list[str]:
    bad = []
    for match in _MONEY.finditer(reply):
        raw = next(g for g in match.groups() if g)
        value = _num(raw)
        if match.group(0).lower().rstrip().endswith("k"):
            value *= 1000
        if value not in allowed:
            bad.append(match.group(0).strip())
    return bad


def clean_reply(text: str, limit: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
