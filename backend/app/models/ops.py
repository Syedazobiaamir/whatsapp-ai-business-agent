"""Operational tables: automation errors and the audit trail."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.enums import ActorType, ErrorStatus, ErrorType, RiskLevel


class AutomationError(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_errors"

    source: Mapped[str] = mapped_column(String(32))          # n8n, backend, whatsapp, gemini
    workflow_name: Mapped[str | None] = mapped_column(String(160))
    workflow_id: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[str | None] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(160))
    error_type: Mapped[ErrorType] = mapped_column(
        str_enum(ErrorType, "error_type"), default=ErrorType.UNKNOWN
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        str_enum(RiskLevel, "risk_level"), default=RiskLevel.RISKY  # unsafe until proven safe
    )
    error_message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # secrets are stripped first
    status: Mapped[ErrorStatus] = mapped_column(
        str_enum(ErrorStatus, "error_status"), default=ErrorStatus.OPEN
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    # --- Recovery (Stage 9) ---
    # What failed, so the recovery runner knows how to retry it:
    # send_message | follow_up | gemini | n8n_execution
    operation: Mapped[str | None] = mapped_column(String(32))
    # Same problem again = same fingerprint -> counted on ONE row instead of flooding the list.
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    occurrences: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # alert sent once

    __table_args__ = (
        Index("ix_automation_errors_status_created", "status", "created_at"),
        Index("ix_automation_errors_retry_due", "status", "next_retry_at"),
        # Only ONE unresolved row per fingerprint (resolved ones are history).
        Index(
            "uq_automation_errors_open_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status IN ('open', 'retrying', 'needs_approval')"),
        ),
        # The same failed n8n node in the same execution is recorded only once.
        Index(
            "uq_automation_errors_execution_node",
            "execution_id",
            "node_name",
            unique=True,
            postgresql_where=text("execution_id IS NOT NULL"),
        ),
    )


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only history of who changed what. No updated_at: rows are never edited."""

    __tablename__ = "audit_logs"

    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "audit_actor_type"))
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64))         # e.g. "lead.status_changed"
    entity_type: Mapped[str] = mapped_column(String(48))    # e.g. "lead"
    entity_id: Mapped[str | None] = mapped_column(String(64))
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {"status": ["new", "won"]}
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_created", "created_at"),
        Index("ix_audit_logs_actor_agent", "actor_agent_id"),
    )
