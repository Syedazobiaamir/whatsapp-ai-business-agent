"""Checks applied to EVERY AI reply before it can reach a customer.

LLMs can "hallucinate" (confidently invent facts). For a business, an invented price or
discount is a real problem, so any amount in a reply must exist in our own data.
Stage 14: also understands Urdu digits (۹۹), Urdu / Roman Urdu currency words (روپے, rupay, ڈالر)
and multipliers (hazar / ہزار = 1,000, lakh / لاکھ = 100,000), so a price cannot sneak through
just because it is written in Urdu.
"""
import re

from app.models import Service

# Urdu (Extended Arabic-Indic) and Arabic-Indic digits -> 0-9
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# (?<![a-z]) = the currency word must not be the END of another word ("hours 9" is not "rs 9")
_BEFORE = r"(?<![a-z])(?:usd|us\$|pkr|rs\.?|eur|gbp)|[$€£]"
_AFTER = (r"(?:usd|dollars?|pkr|rupees?|rupay|rupaye|roopay|eur|euros?|gbp)(?![a-z])"
          r"|ڈالر|روپے|روپیہ|روپیے|%")
_MULT = {"k": 1_000, "hazar": 1_000, "hazaar": 1_000, "ہزار": 1_000, "lakh": 100_000, "لاکھ": 100_000}
_MONEY = re.compile(
    rf"(?P<pre>{_BEFORE})?\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?"
    r"(?P<mult>k(?![a-z])|hazar|hazaar|ہزار|lakh|لاکھ)?\s?"
    rf"(?P<post>{_AFTER})?",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Kept for backwards compatibility; the orchestrator now uses language.reply("safe_price", lang).
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
        allowed.update(_num(n) for n in _NUMBER.findall(text.translate(_DIGITS)))
    return allowed


def find_unapproved_amounts(reply: str, allowed: set[float]) -> list[str]:
    """Every amount that looks like money (currency, multiplier or %) must be in `allowed`."""
    bad = []
    for m in _MONEY.finditer(reply.translate(_DIGITS)):
        if not (m.group("pre") or m.group("mult") or m.group("post")):
            continue                                   # a plain number ("2 to 4 weeks") is not money
        value = _num(m.group("num")) * _MULT.get((m.group("mult") or "").lower(), 1)
        if value not in allowed:
            bad.append(m.group(0).strip())
    return bad


def clean_reply(text: str, limit: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
