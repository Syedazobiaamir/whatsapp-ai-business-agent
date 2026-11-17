"""Customers (WhatsApp users) and human agents (dashboard users)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.enums import AgentRole


class HumanAgent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "human_agents"

    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255))   # bcrypt hash, never the password
    role: Mapped[AgentRole] = mapped_column(
        str_enum(AgentRole, "agent_role"), default=AgentRole.SALES_AGENT
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"

    # WhatsApp ID = phone number in international format without "+", e.g. "923001234567"
    wa_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str | None] = mapped_column(String(120))     # WhatsApp profile name or as told
    email: Mapped[str | None] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)

    # Consent: marketing follow-ups only when opted in and not opted out.
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
