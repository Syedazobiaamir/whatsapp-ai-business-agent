"""Conversations, messages and human handoff requests."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.people import Customer

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.enums import (
    ActorType,
    ConversationMode,
    ConversationStatus,
    HandoffStatus,
    MessageDirection,
    MessageType,
    SendStatus,
    SenderType,
)


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE")
    )
    status: Mapped[ConversationStatus] = mapped_column(
        str_enum(ConversationStatus, "conversation_status"), default=ConversationStatus.OPEN
    )
    mode: Mapped[ConversationMode] = mapped_column(
        str_enum(ConversationMode, "conversation_mode"), default=ConversationMode.AI
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
    # WhatsApp only allows free-form replies within 24h of the customer's last message.
    last_customer_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Short AI-written summary of older messages: keeps the Gemini prompt small.
    summary: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Python-side links (no DB change). lazy="raise": we must load them explicitly
    # (selectinload), so we never trigger hidden slow queries in a loop.
    customer: Mapped["Customer"] = relationship(lazy="raise")
    messages: Mapped[list["Message"]] = relationship(
        lazy="raise", order_by="(Message.created_at, Message.seq)", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_conversations_customer_status", "customer_id", "status"),
        Index("ix_conversations_status_last_message", "status", "last_message_at"),
        # At most ONE open conversation per customer (partial unique index).
        Index(
            "uq_conversations_one_open_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    direction: Mapped[MessageDirection] = mapped_column(
        str_enum(MessageDirection, "message_direction")
    )
    sender_type: Mapped[SenderType] = mapped_column(str_enum(SenderType, "sender_type"))
    sent_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
    # Meta's message ID ("wamid...."). UNIQUE = the same webhook delivered twice
    # cannot create two rows -> no duplicate AI replies. NULL allowed for unsent outbound.
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    message_type: Mapped[MessageType] = mapped_column(
        str_enum(MessageType, "message_type"), default=MessageType.TEXT
    )
    body: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64))       # set by the AI (Stage 6)
    ai_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # confidence, model, ...
    send_status: Mapped[SendStatus] = mapped_column(
        str_enum(SendStatus, "send_status"), default=SendStatus.RECEIVED
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    # Insertion order. Meta timestamps only have 1-second precision, so several messages
    # can share created_at; (created_at, seq) gives a stable, correct order.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)

    # --- AI processing (Stage 6) ---
    # A "claim": set when the AI starts on this inbound message, so two parallel
    # requests (e.g. an n8n retry) cannot both answer it.
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Outbound reply -> the inbound message it answers (lets a retry find the existing reply).
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_reply_to", "reply_to_message_id"),
    )


class HandoffRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "handoff_requests"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    requested_by: Mapped[ActorType] = mapped_column(str_enum(ActorType, "handoff_requested_by"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[HandoffStatus] = mapped_column(
        str_enum(HandoffStatus, "handoff_status"), default=HandoffStatus.PENDING
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_agents.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_handoff_requests_status_created", "status", "created_at"),
        # Only one active (pending/accepted) handoff per conversation.
        Index(
            "uq_handoff_requests_one_active_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'accepted')"),
        ),
    )
