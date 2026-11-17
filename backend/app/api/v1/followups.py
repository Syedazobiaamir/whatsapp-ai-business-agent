"""Follow-up approval on the dashboard: a human approves before any marketing message is sent."""
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import AnyRole, PageParams, SessionDep, StaffOnly
from app.models import Customer, FollowUpTask
from app.models.enums import FollowUpStatus
from app.schemas.business import FollowUpOut
from app.schemas.common import Page
from app.services.audit import paginate, record_audit

router = APIRouter(prefix="/follow-ups", tags=["follow-ups"])


@router.get("", response_model=Page[FollowUpOut])
async def list_follow_ups(_: AnyRole, session: SessionDep, pg: PageParams,
                          task_status: Annotated[FollowUpStatus | None, Query(alias="status")] = None):
    stmt = select(FollowUpTask).order_by(FollowUpTask.created_at.desc())
    if task_status:
        stmt = stmt.where(FollowUpTask.status == task_status)
    items, total = await paginate(session, stmt, pg.page, pg.size)
    # Add the customer's name for the dashboard (one query for the whole page).
    customers = {c.id: c for c in (await session.scalars(
        select(Customer).where(Customer.id.in_([t.customer_id for t in items])))).all()}
    out = [FollowUpOut.model_validate(t).model_copy(update={
        "customer_name": customers[t.customer_id].name, "customer_wa_id": customers[t.customer_id].wa_id})
        for t in items]
    return Page(items=out, total=total, page=pg.page, size=pg.size)


async def _change(task_id, new_status, agent, session, request, action) -> FollowUpTask:
    task = await session.get(FollowUpTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Follow-up not found")
    if task.status != FollowUpStatus.PENDING_APPROVAL and not (
            new_status == FollowUpStatus.CANCELLED and task.status == FollowUpStatus.APPROVED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Follow-up is already {task.status.value}")
    old = task.status
    task.status = new_status
    if new_status == FollowUpStatus.APPROVED:
        task.approved_by_agent_id, task.approved_at = agent.id, datetime.now(UTC)
    await record_audit(session, action=action, entity_type="follow_up", entity_id=task.id, actor=agent,
                       request=request, changes={"status": [old.value, new_status.value]})
    await session.commit()
    await session.refresh(task)
    return task


@router.post("/{task_id}/approve", response_model=FollowUpOut)
async def approve(task_id: uuid.UUID, agent: StaffOnly, session: SessionDep, request: Request):
    return await _change(task_id, FollowUpStatus.APPROVED, agent, session, request, "follow_up.approved")


@router.post("/{task_id}/cancel", response_model=FollowUpOut)
async def cancel(task_id: uuid.UUID, agent: StaffOnly, session: SessionDep, request: Request):
    return await _change(task_id, FollowUpStatus.CANCELLED, agent, session, request, "follow_up.cancelled")
