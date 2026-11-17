"""Customers (people who messaged us on WhatsApp)."""
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import or_, select

from app.api.deps import AnyRole, PageParams, SessionDep, StaffOnly
from app.models import Customer
from app.schemas.common import Page
from app.schemas.crm import CustomerOut, CustomerUpdate
from app.services.audit import apply_changes, paginate, record_audit

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=Page[CustomerOut])
async def list_customers(
    _: AnyRole, session: SessionDep, pg: PageParams,
    q: Annotated[str | None, Query(max_length=100, description="Search name, phone, email, company")] = None,
):
    stmt = select(Customer).order_by(Customer.created_at.desc())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.wa_id.ilike(like),
                              Customer.email.ilike(like), Customer.company.ilike(like)))
    items, total = await paginate(session, stmt, pg.page, pg.size)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: uuid.UUID, _: AnyRole, session: SessionDep):
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, agent: StaffOnly, session: SessionDep,
    request: Request,
):
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("marketing_opt_in", False) is None:
        data.pop("marketing_opt_in")       # consent can be true or false, never "unknown"
    # Turning marketing consent OFF also records when they opted out.
    if data.get("marketing_opt_in") is False and customer.marketing_opt_in:
        data["opted_out_at"] = datetime.now(UTC)
    elif data.get("marketing_opt_in") is True:
        data["opted_out_at"] = None
    changes = apply_changes(customer, data)
    if changes:
        await record_audit(session, action="customer.updated", entity_type="customer",
                           entity_id=customer.id, actor=agent, changes=changes, request=request)
    await session.commit()
    await session.refresh(customer)
    return customer
