"""Leads: search, filter, view and update."""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import AnyRole, PageParams, SessionDep, StaffOnly
from app.models import Customer, HumanAgent, Lead
from app.models.enums import ActorType, LeadStatus, LeadTemperature
from app.schemas.common import Page
from app.schemas.crm import LeadDetail, LeadOut, LeadUpdate
from app.services.audit import apply_changes, paginate, record_audit
from app.services.leads import add_lead_activity

router = APIRouter(prefix="/leads", tags=["leads"])

SORTS = {
    "newest": Lead.created_at.desc(),
    "updated": Lead.updated_at.desc(),
    "score": Lead.score.desc(),
}


@router.get("", response_model=Page[LeadOut])
async def list_leads(
    _: AnyRole,
    session: SessionDep,
    pg: PageParams,
    q: Annotated[str | None, Query(max_length=100, description="Name, phone, company, requirements")] = None,
    temperature: LeadTemperature | None = None,
    lead_status: Annotated[LeadStatus | None, Query(alias="status")] = None,
    assigned_agent_id: uuid.UUID | None = None,
    sort: Literal["newest", "updated", "score"] = "updated",
):
    # join(Customer) lets us search customer fields; selectinload fetches the customer
    # objects for the page in ONE extra query (not one per lead).
    stmt = select(Lead).join(Customer).options(selectinload(Lead.customer)).order_by(SORTS[sort])
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.wa_id.ilike(like),
                              Customer.company.ilike(like), Lead.requirements.ilike(like)))
    if temperature:
        stmt = stmt.where(Lead.temperature == temperature)
    if lead_status:
        stmt = stmt.where(Lead.status == lead_status)
    if assigned_agent_id:
        stmt = stmt.where(Lead.assigned_agent_id == assigned_agent_id)
    items, total = await paginate(session, stmt, pg.page, pg.size)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


async def _load_lead(session, lead_id: uuid.UUID) -> Lead:
    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id)
        .options(selectinload(Lead.customer), selectinload(Lead.activities))
        .execution_options(populate_existing=True)   # always fresh data from the DB
    )
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return lead


@router.get("/{lead_id}", response_model=LeadDetail)
async def get_lead(lead_id: uuid.UUID, _: AnyRole, session: SessionDep):
    return await _load_lead(session, lead_id)


@router.patch("/{lead_id}", response_model=LeadDetail)
async def update_lead(
    lead_id: uuid.UUID, body: LeadUpdate, agent: StaffOnly, session: SessionDep, request: Request
):
    lead = await _load_lead(session, lead_id)
    data = body.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    for field in ("status", "temperature"):          # these columns can never be empty
        if data.get(field, "x") is None:
            data.pop(field)

    if data.get("assigned_agent_id"):
        assignee = await session.get(HumanAgent, data["assigned_agent_id"])
        if assignee is None or not assignee.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Assigned agent not found or inactive")

    changes = apply_changes(lead, data)
    for field, (old, new) in changes.items():
        add_lead_activity(
            session, lead, activity_type=f"{field}_changed",
            description=f"{field.replace('_', ' ').capitalize()} changed from {old} to {new}",
            actor_type=ActorType.AGENT, actor=agent, details={"from": old, "to": new},
        )
    if note:
        add_lead_activity(session, lead, activity_type="note", description=note,
                          actor_type=ActorType.AGENT, actor=agent)
    if changes or note:
        await record_audit(session, action="lead.updated", entity_type="lead", entity_id=lead.id,
                           actor=agent, request=request,
                           changes={**changes, **({"note": note} if note else {})})
    await session.commit()
    return await _load_lead(session, lead_id)
