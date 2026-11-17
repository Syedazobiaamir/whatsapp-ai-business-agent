"""Lead helpers shared by the API (and later by the AI modules)."""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HumanAgent, Lead, LeadActivity
from app.models.enums import ActorType


def add_lead_activity(
    session: AsyncSession,
    lead: Lead,
    *,
    activity_type: str,
    description: str,
    actor_type: ActorType,
    actor: HumanAgent | None = None,
    details: dict[str, Any] | None = None,
) -> LeadActivity:
    """Add one entry to the lead's timeline (shown on the lead detail page)."""
    activity = LeadActivity(
        lead_id=lead.id,
        activity_type=activity_type,
        description=description,
        actor_type=actor_type,
        actor_agent_id=actor.id if actor else None,
        details=details,
    )
    session.add(activity)
    return activity
