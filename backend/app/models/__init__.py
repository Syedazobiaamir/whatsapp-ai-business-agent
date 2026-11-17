"""Import every model here so Base.metadata knows all tables (Alembic relies on this)."""
from app.models.base import Base
from app.models.business import AppSetting, BusinessFAQ, Service
from app.models.conversation import Conversation, HandoffRequest, Message
from app.models.lead import FollowUpTask, Lead, LeadActivity
from app.models.ops import AuditLog, AutomationError
from app.models.people import Customer, HumanAgent

__all__ = [
    "AppSetting",
    "AuditLog",
    "AutomationError",
    "Base",
    "BusinessFAQ",
    "Conversation",
    "Customer",
    "FollowUpTask",
    "HandoffRequest",
    "HumanAgent",
    "Lead",
    "LeadActivity",
    "Message",
    "Service",
]
