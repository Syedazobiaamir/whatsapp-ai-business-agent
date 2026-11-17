"""Customers, leads, conversations, messages, handoffs."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import (
    ActorType,
    ConversationMode,
    ConversationStatus,
    HandoffStatus,
    LeadStatus,
    LeadTemperature,
    MessageDirection,
    MessageType,
    SendStatus,
    SenderType,
)
from app.schemas.common import ORMModel


# ---------- Customers ----------
class CustomerOut(ORMModel):
    id: uuid.UUID
    wa_id: str
    name: str | None
    email: str | None
    company: str | None
    notes: str | None
    marketing_opt_in: bool
    opted_out_at: datetime | None
    last_seen_at: datetime | None
    language: str | None = None
    created_at: datetime


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    company: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=5000)
    marketing_opt_in: bool | None = None


# ---------- Leads ----------
class LeadActivityOut(ORMModel):
    id: uuid.UUID
    activity_type: str
    description: str
    details: dict[str, Any] | None
    actor_type: ActorType
    actor_agent_id: uuid.UUID | None
    created_at: datetime


class LeadOut(ORMModel):
    id: uuid.UUID
    customer: CustomerOut
    conversation_id: uuid.UUID | None
    assigned_agent_id: uuid.UUID | None
    temperature: LeadTemperature
    score: int
    qualification_reason: str | None
    last_qualified_at: datetime | None
    status: LeadStatus
    requirements: str | None
    interested_services: list[str] | None
    budget_text: str | None
    estimated_budget: Decimal | None
    budget_currency: str | None
    timeline: str | None
    next_follow_up_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeadDetail(LeadOut):
    activities: list[LeadActivityOut]


class LeadUpdate(BaseModel):
    """What a human may change on the dashboard. Temperature/score are set by the AI,
    but a human can override the temperature (recorded in the activity log)."""
    status: LeadStatus | None = None
    temperature: LeadTemperature | None = None
    assigned_agent_id: uuid.UUID | None = None
    next_follow_up_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)   # stored as an activity


# ---------- Conversations ----------
class MessageOut(ORMModel):
    id: uuid.UUID
    direction: MessageDirection
    sender_type: SenderType
    sent_by_agent_id: uuid.UUID | None
    wa_message_id: str | None
    message_type: MessageType
    body: str | None
    intent: str | None
    ai_metadata: dict[str, Any] | None
    send_status: SendStatus
    error_message: str | None
    created_at: datetime


class ConversationOut(ORMModel):
    id: uuid.UUID
    customer: CustomerOut
    status: ConversationStatus
    mode: ConversationMode
    assigned_agent_id: uuid.UUID | None
    last_customer_message_at: datetime | None
    last_message_at: datetime | None
    summary: str | None
    created_at: datetime
    last_message_preview: str | None = None
    has_pending_handoff: bool = False


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    within_24h_window: bool    # can we send a free-form reply right now?


class TakeoverRequest(BaseModel):
    reason: str = Field(default="Taken over from dashboard", max_length=500)


class ReplyRequest(BaseModel):
    # WhatsApp's text limit is 4096 characters. strip whitespace-only messages.
    text: str = Field(min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message cannot be empty")
        return v.strip()


# ---------- Handoffs ----------
class HandoffOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    requested_by: ActorType
    reason: str
    status: HandoffStatus
    assigned_agent_id: uuid.UUID | None
    accepted_at: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime
