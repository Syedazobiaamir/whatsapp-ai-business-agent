"""The JSON shapes Gemini must return ("structured output").

Field descriptions are sent to Gemini as part of the JSON schema, so they double as instructions.
"""
from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "greeting",          # hi / hello / salam
    "faq",               # general question answered by the knowledge base
    "pricing",           # asks about cost / quote
    "service_inquiry",   # asks what we offer / details of a service
    "buying_intent",     # clearly wants to buy / start / book a call
    "human_request",     # wants a person
    "complaint",         # unhappy / problem
    "other",
]
SALES_INTENTS = {"pricing", "service_inquiry", "buying_intent"}


class SupportResult(BaseModel):
    intent: Intent = Field(description="The customer's main intent in their LATEST message.")
    confidence: float = Field(ge=0, le=1, description="How sure you are the answer is correct and "
                              "fully supported by the FAQ / service data (0-1).")
    answer: str = Field(max_length=1500, description="Reply to send to the customer. Use ONLY "
                        "facts from the provided FAQs, services and business info.")
    used_faq_ids: list[str] = Field(default=[], description="IDs of FAQs the answer is based on.")
    needs_human: bool = Field(description="True if a human must handle this (complaint, "
                              "explicit request for a person, or you cannot answer).")
    reasoning: str = Field(max_length=300, description="One short sentence explaining the decision.")


class LeadFacts(BaseModel):
    """Facts the customer has stated. null = not mentioned. Never guess."""
    customer_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=160)
    requirements: str | None = Field(default=None, max_length=1000,
                                     description="What they want built / need, in one or two sentences.")
    interested_services: list[str] = Field(default=[], description="Exact service names from the catalog.")
    budget_text: str | None = Field(default=None, max_length=120, description="Budget as they said it.")
    budget_amount: float | None = Field(default=None, ge=0, description="Budget as a number, if stated.")
    budget_currency: str | None = Field(default=None, description="ISO currency code, e.g. USD, PKR.")
    timeline: str | None = Field(default=None, max_length=120, description="When they want it, as said.")
    timeline_days: int | None = Field(default=None, ge=0, le=3650,
                                      description="Estimated days until they want to start.")
    is_decision_maker: bool | None = Field(default=None,
                                           description="True if they decide / pay, false if someone else does.")
    buying_signals: list[str] = Field(default=[], description="Short phrases showing intent to buy, "
                                      "e.g. 'asked for a quote', 'wants to start this week'.")


class SalesResult(BaseModel):
    reply: str = Field(max_length=1500, description="Friendly reply that answers the question and asks "
                       "at most ONE qualifying question about what is still unknown.")
    facts: LeadFacts
    recommended_services: list[str] = Field(default=[], description="Exact names from the catalog only.")
    next_question_topic: Literal["need", "budget", "timeline", "decision_maker", "contact", "none"]
