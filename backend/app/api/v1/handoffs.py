"""Human handoff requests (read-only here; the workflow is built in Stage 7)."""
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import AnyRole, PageParams, SessionDep
from app.models import HandoffRequest
from app.models.enums import HandoffStatus
from app.schemas.common import Page
from app.schemas.crm import HandoffOut
from app.services.audit import paginate

router = APIRouter(prefix="/handoffs", tags=["handoffs"])


@router.get("", response_model=Page[HandoffOut])
async def list_handoffs(
    _: AnyRole, session: SessionDep, pg: PageParams,
    handoff_status: Annotated[HandoffStatus | None, Query(alias="status")] = None,
):
    stmt = select(HandoffRequest).order_by(HandoffRequest.created_at.desc())
    if handoff_status:
        stmt = stmt.where(HandoffRequest.status == handoff_status)
    items, total = await paginate(session, stmt, pg.page, pg.size)
    return Page(items=items, total=total, page=pg.page, size=pg.size)
