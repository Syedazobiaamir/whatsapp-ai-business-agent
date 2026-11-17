"""Validate Meta's webhook JSON and turn it into simple Python objects.

Meta's format (one POST can contain several events, in any order):
{"object": "whatsapp_business_account",
 "entry": [{"id": "...", "changes": [{"field": "messages", "value": {
      "messaging_product": "whatsapp",
      "metadata": {"display_phone_number": "...", "phone_number_id": "..."},
      "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
      "messages": [{"from": "...", "id": "wamid...", "timestamp": "1749416383",
                    "type": "text", "text": {"body": "..."}}],
      "statuses": [{"id": "wamid...", "status": "delivered", "timestamp": "...",
                    "recipient_id": "...", "errors": [...]}]}}]}]}
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MessageType, SendStatus

# ---------------- Pydantic models of Meta's JSON (extra fields allowed) ----------------


class _Flexible(BaseModel):
    # Meta adds new fields over time; unknown fields must not break us.
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class WAProfile(_Flexible):
    name: str | None = None


class WAContact(_Flexible):
    profile: WAProfile | None = None
    wa_id: str | None = None
    user_id: str | None = None


class WAMessage(_Flexible):
    from_: str | None = Field(default=None, alias="from")   # "from" is a Python keyword
    from_user_id: str | None = None
    id: str = Field(min_length=1, max_length=128)
    timestamp: str
    type: str
    text: dict[str, Any] | None = None
    button: dict[str, Any] | None = None
    interactive: dict[str, Any] | None = None


class WAStatus(_Flexible):
    id: str = Field(min_length=1, max_length=128)
    status: str
    timestamp: str
    recipient_id: str | None = None
    errors: list[dict[str, Any]] | None = None


class WAValue(_Flexible):
    messaging_product: str
    metadata: dict[str, Any] = {}
    contacts: list[WAContact] = []
    messages: list[WAMessage] = []
    statuses: list[WAStatus] = []


class WAChange(_Flexible):
    field: str
    value: WAValue


class WAEntry(_Flexible):
    id: str
    changes: list[WAChange] = []


class WAWebhook(_Flexible):
    object: str
    entry: list[WAEntry] = []


# ---------------- Simple objects the rest of the app works with ----------------


@dataclass
class InboundMessage:
    wa_message_id: str
    wa_id: str                      # sender (phone number or Meta user id)
    profile_name: str | None
    message_type: MessageType
    body: str | None                # text we can show / give to the AI
    sent_at: datetime
    phone_number_id: str | None     # which of OUR numbers received it
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusUpdate:
    wa_message_id: str
    status: SendStatus
    sent_at: datetime
    error: str | None


_TYPE_MAP = {
    "text": MessageType.TEXT,
    "button": MessageType.INTERACTIVE,
    "interactive": MessageType.INTERACTIVE,
    "image": MessageType.IMAGE,
    "document": MessageType.DOCUMENT,
    "audio": MessageType.AUDIO,
}

_STATUS_MAP = {
    "sent": SendStatus.SENT,
    "delivered": SendStatus.DELIVERED,
    "read": SendStatus.READ,
    "played": SendStatus.READ,      # voice message listened to = read
    "failed": SendStatus.FAILED,
}


def _to_datetime(unix_seconds: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(unix_seconds), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _extract_text(msg: WAMessage) -> str | None:
    """Get readable text out of any message type."""
    if msg.type == "text" and msg.text:
        return msg.text.get("body")
    if msg.type == "button" and msg.button:
        return msg.button.get("text")
    if msg.type == "interactive" and msg.interactive:
        reply = msg.interactive.get("button_reply") or msg.interactive.get("list_reply") or {}
        return reply.get("title")
    media = getattr(msg, msg.type, None) if msg.type in ("image", "document", "video") else None
    if isinstance(media, dict) and media.get("caption"):
        return media["caption"]
    return f"[{msg.type} message]"   # e.g. sticker/location: shown as a placeholder


def extract_events(webhook: WAWebhook) -> tuple[list[InboundMessage], list[StatusUpdate]]:
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    for entry in webhook.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            value = change.value
            names = {
                (c.wa_id or c.user_id): (c.profile.name if c.profile else None)
                for c in value.contacts
            }
            phone_number_id = value.metadata.get("phone_number_id")
            for m in value.messages:
                sender = m.from_ or m.from_user_id
                if not sender:
                    continue
                messages.append(InboundMessage(
                    wa_message_id=m.id,
                    wa_id=sender,
                    profile_name=names.get(sender),
                    message_type=_TYPE_MAP.get(m.type, MessageType.OTHER),
                    body=_extract_text(m),
                    sent_at=_to_datetime(m.timestamp),
                    phone_number_id=phone_number_id,
                    raw=m.model_dump(by_alias=True),
                ))
            for s in value.statuses:
                mapped = _STATUS_MAP.get(s.status)
                if mapped is None:
                    continue
                error = None
                if s.errors:
                    e = s.errors[0]
                    error = f"{e.get('code')}: {e.get('title') or e.get('message')}"
                statuses.append(StatusUpdate(s.id, mapped, _to_datetime(s.timestamp), error))
    return messages, statuses
