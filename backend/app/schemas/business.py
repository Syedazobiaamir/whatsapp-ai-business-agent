"""FAQs, services, settings, stats, audit log."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from app.models.enums import ActorType
from app.schemas.common import ORMModel


# ---------- FAQs ----------
class FAQBase(BaseModel):
    question: str = Field(min_length=5, max_length=500)
    answer: str = Field(min_length=2, max_length=4000)
    category: str = Field(default="general", max_length=64)
    keywords: str | None = Field(default=None, max_length=500)
    priority: int = Field(default=0, ge=0, le=100)
    is_active: bool = True


class FAQCreate(FAQBase):
    pass


class FAQUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=5, max_length=500)
    answer: str | None = Field(default=None, min_length=2, max_length=4000)
    category: str | None = Field(default=None, max_length=64)
    keywords: str | None = Field(default=None, max_length=500)
    priority: int | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class FAQOut(ORMModel, FAQBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------- Services ----------
class ServiceBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=5, max_length=2000)
    price_from: Decimal | None = Field(default=None, ge=0)
    price_to: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    is_active: bool = True

    @model_validator(mode="after")
    def check_price_range(self):
        if self.price_from is not None and self.price_to is not None and self.price_to < self.price_from:
            raise ValueError("price_to must be >= price_from")
        return self


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, min_length=5, max_length=2000)
    price_from: Decimal | None = Field(default=None, ge=0)
    price_to: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    is_active: bool | None = None


class ServiceOut(ORMModel, ServiceBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------- Settings (each key has its own validated shape) ----------
class BusinessInfo(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(max_length=1000)
    website: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    timezone: str = Field(default="UTC", max_length=64)
    business_hours: str = Field(max_length=255)
    languages: list[str] = ["English"]


class AIConfig(BaseModel):
    model: str = Field(max_length=64)
    temperature: float = Field(ge=0, le=1)       # low = predictable answers
    tone: str = Field(max_length=200)
    max_history_messages: int = Field(ge=2, le=50)
    faq_confidence_threshold: float = Field(ge=0, le=1)
    handoff_keywords: list[str]
    never_invent_prices: bool = True


class LeadRules(BaseModel):
    hot_min_score: int = Field(ge=1, le=100)
    warm_min_score: int = Field(ge=0, le=99)
    qualifying_questions: list[str]

    @model_validator(mode="after")
    def check_order(self):
        if self.warm_min_score >= self.hot_min_score:
            raise ValueError("warm_min_score must be lower than hot_min_score")
        return self


class FollowUpRules(BaseModel):
    enabled: bool
    first_follow_up_after_hours: int = Field(ge=1, le=720)
    second_follow_up_after_hours: int = Field(ge=1, le=720)
    max_follow_ups: int = Field(ge=0, le=5)
    require_human_approval: bool = True
    template_name: str = Field(max_length=120)
    template_uses_customer_name: bool = False    # True only if the template body has {{1}}


class HandoffRules(BaseModel):
    # Give a conversation back to the AI after this many hours with no activity at all.
    auto_resume_after_hours: int = Field(default=24, ge=1, le=720)


# Only these keys can be stored, each validated by its schema.
SETTINGS_SCHEMAS: dict[str, type[BaseModel]] = {
    "business_info": BusinessInfo,
    "ai_config": AIConfig,
    "lead_rules": LeadRules,
    "follow_up_rules": FollowUpRules,
    "handoff_rules": HandoffRules,
}


# ---------- Follow-ups ----------
class FollowUpOut(ORMModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    customer_id: uuid.UUID
    scheduled_at: datetime
    message_type: str
    template_name: str | None
    message_body: str | None
    status: str
    requires_approval: bool
    approved_by_agent_id: uuid.UUID | None
    approved_at: datetime | None
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    customer_name: str | None = None
    customer_wa_id: str | None = None


class SettingOut(ORMModel):
    key: str
    value: dict[str, Any]
    updated_at: datetime
    updated_by_agent_id: uuid.UUID | None


# ---------- Stats ----------
class OverviewStats(BaseModel):
    total_customers: int
    total_leads: int
    hot_leads: int
    warm_leads: int
    cold_leads: int
    active_conversations: int
    conversations_in_human_mode: int
    pending_handoffs: int
    automation_failures_open: int
    leads_by_status: dict[str, int]
    messages_last_24h: int


# ---------- Audit ----------
class AuditLogOut(ORMModel):
    id: uuid.UUID
    actor_type: ActorType
    actor_agent_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    changes: dict[str, Any] | None
    # The DB returns an IPv4Address object; convert it to a plain string for JSON.
    ip_address: Annotated[str | None, BeforeValidator(lambda v: None if v is None else str(v))]
    created_at: datetime
