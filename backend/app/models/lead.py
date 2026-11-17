"""Leads, their activity timeline, and scheduled follow-ups."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.enums import ActorType, FollowUpStatus, LeadStatus, LeadTemperature, MessageType

if TYPE_CHECKING:
    from app.models.people import Customer


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    # One lead record per customer (unique); it is updated as we learn more.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), unique=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )

    # AI qualification
    temperature: Mapped[LeadTemperature] = mapped_column(
        str_enum(LeadTemperature, "lead_temperature"), default=LeadTemperature.COLD
    )
    score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    qualification_reason: Mapped[str | None] = mapped_column(Text)
    last_qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Sales pipeline status (changed by humans on the dashboard)
    status: Mapped[LeadStatus] = mapped_column(
        str_enum(LeadStatus, "lead_status"), default=LeadStatus.NEW
    )

    # What we learned from the conversation
    requirements: Mapped[str | None] = mapped_column(Text)
    interested_services: Mapped[list[str] | None] = mapped_column(JSONB)
    budget_text: Mapped[str | None] = mapped_column(String(120))   # as the customer said it
    estimated_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_currency: Mapped[str | None] = mapped_column(String(3))  # ISO code, e.g. "USD"
    timeline: Mapped[str | None] = mapped_column(String(120))
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Qualification signals remembered across messages (Stage 6)
    timeline_days: Mapped[int | None] = mapped_column(Integer)          # days until they want to start
    is_decision_maker: Mapped[bool | None] = mapped_column(Boolean)
    buying_signals: Mapped[list[str] | None] = mapped_column(JSONB)

    customer: Mapped["Customer"] = relationship(lazy="raise")
    activities: Mapped[list["LeadActivity"]] = relationship(
        lazy="raise", order_by="LeadActivity.created_at.desc()", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        Index("ix_leads_temperature", "temperature"),
        Index("ix_leads_status", "status"),
        Index("ix_leads_assigned_agent", "assigned_agent_id"),
    )


class LeadActivity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lead_activities"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE")
    )
    activity_type: Mapped[str] = mapped_column(String(48))  # created, qualified, status_changed...
    description: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "lead_activity_actor"))
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_lead_activities_lead_created", "lead_id", "created_at"),)


class FollowUpTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "follow_up_tasks"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE")
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Outside the 24h window WhatsApp requires an approved template.
    message_type: Mapped[MessageType] = mapped_column(
        str_enum(MessageType, "follow_up_message_type"), default=MessageType.TEMPLATE
    )
    template_name: Mapped[str | None] = mapped_column(String(120))
    template_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    message_body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FollowUpStatus] = mapped_column(
        str_enum(FollowUpStatus, "follow_up_status"), default=FollowUpStatus.PENDING_APPROVAL
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True)
    approved_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    # e.g. "lead:<id>:followup:1" - the same follow-up can never be created twice
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)

    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_range"),
        Index("ix_follow_up_tasks_status_scheduled", "status", "scheduled_at"),
        Index("ix_follow_up_tasks_lead", "lead_id"),
    )
