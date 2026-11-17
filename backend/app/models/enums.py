"""Fixed value lists used by the models. StrEnum means each member is also a plain string."""
from enum import StrEnum


class AgentRole(StrEnum):
    ADMIN = "admin"              # everything, incl. settings, team, risky recovery approvals
    SALES_AGENT = "sales_agent"  # leads + conversations
    VIEWER = "viewer"            # read-only


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ConversationMode(StrEnum):
    AI = "ai"        # AI replies automatically
    HUMAN = "human"  # a person has taken over; AI is paused


class MessageDirection(StrEnum):
    INBOUND = "inbound"    # customer -> business
    OUTBOUND = "outbound"  # business -> customer


class SenderType(StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"


class MessageType(StrEnum):
    TEXT = "text"
    TEMPLATE = "template"
    INTERACTIVE = "interactive"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    OTHER = "other"


class SendStatus(StrEnum):
    RECEIVED = "received"    # inbound message stored
    PENDING = "pending"      # outbound, not sent yet
    SENT = "sent"            # Meta accepted it
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    MOCKED = "mocked"        # mock mode: logged, never sent to Meta


class LeadTemperature(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


class ActorType(StrEnum):
    AI = "ai"
    AGENT = "agent"    # a human dashboard user
    SYSTEM = "system"
    N8N = "n8n"
    CUSTOMER = "customer"


class HandoffStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class FollowUpStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"   # e.g. customer opted out before send time


class ErrorType(StrEnum):
    TEMPORARY = "temporary"    # timeouts, 5xx: usually safe to retry
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"              # expired/invalid token: retrying will not help
    VALIDATION = "validation"  # bad payload: retrying will not help
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    SAFE = "safe"    # retry cannot cause harm (e.g. read-only call)
    RISKY = "risky"  # retry might duplicate a customer message -> needs human approval


class ErrorStatus(StrEnum):
    OPEN = "open"
    RETRYING = "retrying"
    NEEDS_APPROVAL = "needs_approval"
    RESOLVED = "resolved"
    IGNORED = "ignored"
