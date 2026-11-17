"""Dashboard numbers (Overview page) and the audit log."""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AdminOnly, AnyRole, PageParams, SessionDep
from app.models import AuditLog, AutomationError, Conversation, Customer, HandoffRequest, Lead, Message
from app.models.enums import (
    ConversationMode,
    ConversationStatus,
    ErrorStatus,
    HandoffStatus,
    LeadTemperature,
)
from app.schemas.business import AuditLogOut, OverviewStats
from app.schemas.common import Page
from app.services.audit import paginate

router = APIRouter(tags=["dashboard"])


@router.get("/stats/overview", response_model=OverviewStats)
async def overview(_: AnyRole, session: SessionDep):
    def count(model, *where):
        return select(func.count()).select_from(model).where(*where).scalar_subquery()

    unresolved_errors = (ErrorStatus.OPEN, ErrorStatus.RETRYING, ErrorStatus.NEEDS_APPROVAL)
    # One round-trip to the database: all counts are sub-queries of a single SELECT.
    row = (await session.execute(select(
        count(Customer).label("total_customers"),
        count(Lead).label("total_leads"),
        count(Lead, Lead.temperature == LeadTemperature.HOT).label("hot_leads"),
        count(Lead, Lead.temperature == LeadTemperature.WARM).label("warm_leads"),
        count(Lead, Lead.temperature == LeadTemperature.COLD).label("cold_leads"),
        count(Conversation, Conversation.status == ConversationStatus.OPEN).label("active_conversations"),
        count(Conversation, Conversation.status == ConversationStatus.OPEN,
              Conversation.mode == ConversationMode.HUMAN).label("conversations_in_human_mode"),
        count(HandoffRequest, HandoffRequest.status == HandoffStatus.PENDING).label("pending_handoffs"),
        count(AutomationError, AutomationError.status.in_(unresolved_errors)).label("automation_failures_open"),
        count(Message, Message.created_at >= datetime.now(UTC) - timedelta(hours=24)).label("messages_last_24h"),
    ))).one()

    by_status = dict((await session.execute(
        select(Lead.status, func.count()).group_by(Lead.status))).all())
    return OverviewStats(**row._asdict(),
                         leads_by_status={s.value: n for s, n in by_status.items()})


@router.get("/audit-logs", response_model=Page[AuditLogOut])
async def audit_logs(
    _: AdminOnly, session: SessionDep, pg: PageParams,
    entity_type: Annotated[str | None, Query(max_length=48)] = None,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    actor_agent_id: uuid.UUID | None = None,
):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_agent_id:
        stmt = stmt.where(AuditLog.actor_agent_id == actor_agent_id)
    items, total = await paginate(session, stmt, pg.page, pg.size)
    return Page(items=items, total=total, page=pg.page, size=pg.size)
