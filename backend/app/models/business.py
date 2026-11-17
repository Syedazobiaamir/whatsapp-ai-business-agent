"""Business knowledge: FAQs, the service catalog and editable settings."""
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Computed, ForeignKey, Index, Integer, Numeric, String, Text, true
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BusinessFAQ(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_faqs"

    question: Mapped[str] = mapped_column(Text, unique=True)
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="general")
    keywords: Mapped[str | None] = mapped_column(Text)  # "price, cost, quote"
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True)
    # en | ur | roman_ur: the FAQ fallback prefers answers in the customer's language (Stage 14)
    language: Mapped[str] = mapped_column(String(12), default="en", server_default="en")

    # Full-text search vector, computed by PostgreSQL itself whenever a row changes.
    # The Support Agent searches this to find relevant FAQs for a customer question.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(question, '') || ' ' || coalesce(answer, '')"
            " || ' ' || coalesce(keywords, ''))",
            persisted=True,
        ),
    )

    __table_args__ = (
        Index("ix_business_faqs_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_business_faqs_category_active", "category", "is_active"),
        Index("ix_business_faqs_language_active", "language", "is_active"),
    )


class Service(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Service catalog. The Sales Agent may only quote prices that exist here."""

    __tablename__ = "services"

    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text)
    price_from: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_to: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True)


class AppSetting(TimestampMixin, Base):
    """Key/value settings editable on the dashboard (business info, AI config, ...)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
