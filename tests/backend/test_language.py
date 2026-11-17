"""Stage 14: Urdu / Roman Urdu support."""
import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.ai.context import search_faqs
from app.ai.guardrails import allowed_amounts, find_unapproved_amounts
from app.ai.language import detect_language
from app.ai.llm import LLMError
from app.ai.schemas import SupportResult
from app.api.deps import get_llm_factory
from app.api.v1.dev import build_meta_payload
from app.config import PROJECT_ROOT, get_settings
from app.main import app
from app.models import Customer, Message, Service
from app.models.enums import ErrorType, MessageDirection
from app.seed import seed_business
from app.services.messaging import process_webhook
from app.services.whatsapp import WAWebhook

KEY = "test-internal-key-0123456789"
H = {"X-Internal-Api-Key": KEY}


# ------------------------------------------------------------------ detection
@pytest.mark.parametrize(("text", "lang", "confident"), [
    ("How much does a website cost?", "en", True),
    ("ویب سائٹ کی قیمت کیا ہے؟", "ur", True),
    ("website kitne ki banti hai?", "roman_ur", True),
    ("Hi, mujhe online store chahiye", "roman_ur", True),
    ("Assalam o alaikum", "roman_ur", True),
    ("shukriya", "roman_ur", True),
    ("STOP", "en", False),                       # too short: use the customer's last language
    ("ok", "en", False),
    ("", "en", False),
    ("The main thing is to start soon", "en", True),   # "main", "is", "to" are not counted as Urdu
])
def test_detect_language(text, lang, confident):
    assert detect_language(text) == (lang, confident)


# ------------------------------------------------------------------ guardrail
SERVICES = [Service(name="Business Website", description="x", price_from=400, price_to=1200, currency="USD")]


@pytest.mark.parametrize(("reply", "bad"), [
    ("ویب سائٹ صرف ۹۹ ڈالر میں!", ["99 ڈالر"]),            # Urdu digits + Urdu currency word
    ("Sirf 5 hazar rupay mein website", ["5 hazar rupay"]),  # multiplier: 5,000
    ("قیمت 2 لاکھ روپے", ["2 لاکھ روپے"]),                   # 200,000
    ("Website 400 se 1,200 USD tak", []),                   # catalog prices are fine
    ("قیمت ۴۰۰ سے ۱۲۰۰ USD", []),                          # same prices in Urdu digits are fine
    ("Our office hours 9 to 5", []),                        # regression: "hours" is not "rs"
    ("Delivery in 2 to 4 weeks", []),                       # plain numbers are not money
])
def test_guardrail_understands_urdu_amounts(reply, bad):
    assert find_unapproved_amounts(reply, allowed_amounts(SERVICES, [])) == bad


# ------------------------------------------------------------------ end-to-end through the agent
class NoGemini:
    model = "none"

    async def generate_json(self, **_):
        raise LLMError("GEMINI_API_KEY is not set", ErrorType.AUTH)


class FakeGemini:
    model = "fake"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    async def generate_json(self, *, system, prompt, schema, temperature):
        self.prompts.append(prompt)
        return SupportResult(intent="faq", confidence=0.9, answer=self.answer, used_faq_ids=[],
                             needs_human=False, reasoning="t")


@pytest.fixture
async def agent(client, session):
    data = json.loads((PROJECT_ROOT / "database/seed/business.json").read_text(encoding="utf-8"))
    await seed_business(session, data)
    await session.commit()
    s = get_settings().model_copy(update={"backend_internal_api_key": SecretStr(KEY), "wa_mock_mode": True,
                                          "wa_phone_number_id": ""})
    app.dependency_overrides[get_settings] = lambda: s

    def use(llm):
        app.dependency_overrides[get_llm_factory] = lambda: (lambda model: llm)

    async def say(text, wa_id="923001112222"):
        payload = build_meta_payload(wa_id, "Test", text, f"wamid.L.{datetime.now(UTC).timestamp()}")
        mid = (await process_webhook(session, WAWebhook.model_validate(payload), s)).new_message_ids[0]
        return (await client.post("/api/v1/agent/process", json={"message_id": str(mid)}, headers=H)).json()
    return use, say


async def test_urdu_question_gets_urdu_faq_when_ai_is_down(agent, session):
    use, say = agent
    use(NoGemini())
    d = await say("ویب سائٹ کی قیمت کیا ہے؟")
    assert d["fallback_used"] and d["reply_text"].startswith("بزنس ویب سائٹ")
    customer = await session.scalar(select(Customer))
    await session.refresh(customer)
    assert customer.language == "ur"                              # remembered


async def test_roman_urdu_question_gets_roman_urdu_faq(agent):
    use, say = agent
    use(NoGemini())
    d = await say("online store kitne ka banta hai?")
    assert "E-commerce store aam taur par" in d["reply_text"]


@pytest.mark.parametrize(("text", "starts"), [
    ("Mujhe kisi insaan se baat karni hai", "Intezar ka shukriya"),
    ("مجھے کسی انسان سے بات کرنی ہے", "آپ کے انتظار کا شکریہ"),
    ("Can I talk to a human?", "Thanks for your patience"),
])
async def test_handoff_in_customers_language(agent, text, starts):
    use, say = agent
    use(NoGemini())                                   # handoff must work without any AI
    d = await say(text)
    assert d["action"] == "handoff" and d["reply_text"].startswith(starts)


async def test_stop_confirmation_uses_remembered_language(agent):
    use, say = agent
    use(FakeGemini("جی ضرور"))
    await say("آپ کے اوقاتِ کار کیا ہیں؟")                   # customer writes in Urdu first
    d = await say("STOP")                                      # "STOP" alone is not Urdu...
    assert d["intent"] == "opt_out" and "مارکیٹنگ" in d["reply_text"]   # ...but the reply is


async def test_guardrail_reply_in_customers_language(agent):
    use, say = agent
    use(FakeGemini("Website sirf 99 dollars mein ban jayegi!"))
    d = await say("website kitne ki banti hai?")
    assert d["guardrails"] and d["reply_text"].startswith("Poochne ka shukriya")


async def test_prompt_tells_gemini_the_language(agent):
    use, say = agent
    fake = FakeGemini("Ji, hum international clients ke saath kaam karte hain.")
    use(fake)
    await say("Kya aap international clients ke sath kaam karte hain?")
    assert "Customer language: Roman Urdu" in fake.prompts[0]


async def test_urdu_search_ignores_filler_words(agent, session):
    hits = await search_faqs(session, "ویب سائٹ کی قیمت کیا ہے", language="ur")
    assert hits[0].question == "ویب سائٹ کی قیمت کیا ہے؟"


async def test_faq_language_filter(agent, client, make_agent):
    _, h = await make_agent()
    r = (await client.get("/api/v1/faqs", params={"language": "ur", "size": 50}, headers=h)).json()
    assert r["total"] == 4 and {f["language"] for f in r["items"]} == {"ur"}
    assert (await client.get("/api/v1/faqs", params={"language": "fr"}, headers=h)).status_code == 422
