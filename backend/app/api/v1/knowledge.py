"""Knowledge base: FAQs and the service catalog (what the AI is allowed to say)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import AdminOnly, AnyRole, PageParams, SessionDep
from app.models import BusinessFAQ, Service
from app.schemas.business import (
    FAQCreate,
    FAQOut,
    FAQUpdate,
    ServiceCreate,
    ServiceOut,
    ServiceUpdate,
)
from app.schemas.common import Page
from app.services.audit import apply_changes, paginate, record_audit, to_jsonable

router = APIRouter(tags=["knowledge base"])


# ------------------------------- FAQs -------------------------------
@router.get("/faqs", response_model=Page[FAQOut])
async def list_faqs(
    _: AnyRole, session: SessionDep, pg: PageParams,
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: str | None = None,
    active: bool | None = None,
    language: Annotated[str | None, Query(pattern="^(en|ur|roman_ur)$")] = None,
):
    stmt = select(BusinessFAQ).order_by(BusinessFAQ.priority.desc(), BusinessFAQ.question)
    if q:
        # Full-text search ("online shop" finds "online store ... shop") OR simple substring.
        tsq = func.websearch_to_tsquery("english", q)
        stmt = stmt.where(or_(BusinessFAQ.search_vector.op("@@")(tsq),
                              BusinessFAQ.question.ilike(f"%{q.strip()}%")))
    if category:
        stmt = stmt.where(BusinessFAQ.category == category)
    if active is not None:
        stmt = stmt.where(BusinessFAQ.is_active == active)
    if language:
        stmt = stmt.where(BusinessFAQ.language == language)
    items, total = await paginate(session, stmt, pg.page, pg.size)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


async def _question_taken(session, question: str, exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(BusinessFAQ.id).where(func.lower(BusinessFAQ.question) == question.strip().lower())
    if exclude_id:
        stmt = stmt.where(BusinessFAQ.id != exclude_id)
    return await session.scalar(stmt) is not None


@router.post("/faqs", response_model=FAQOut, status_code=status.HTTP_201_CREATED)
async def create_faq(body: FAQCreate, admin: AdminOnly, session: SessionDep, request: Request):
    if await _question_taken(session, body.question):
        raise HTTPException(status.HTTP_409_CONFLICT, "This question already exists")
    faq = BusinessFAQ(**body.model_dump())
    session.add(faq)
    await session.flush()
    await record_audit(session, action="faq.created", entity_type="faq", entity_id=faq.id,
                       actor=admin, request=request, changes={"question": faq.question})
    await session.commit()
    await session.refresh(faq)
    return faq


@router.patch("/faqs/{faq_id}", response_model=FAQOut)
async def update_faq(faq_id: uuid.UUID, body: FAQUpdate, admin: AdminOnly, session: SessionDep,
                     request: Request):
    faq = await session.get(BusinessFAQ, faq_id)
    if faq is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found")
    data = body.model_dump(exclude_unset=True)
    data = {k: v for k, v in data.items() if v is not None or k == "keywords"}
    if "question" in data and await _question_taken(session, data["question"], faq.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This question already exists")
    changes = apply_changes(faq, data)
    if changes:
        await record_audit(session, action="faq.updated", entity_type="faq", entity_id=faq.id,
                           actor=admin, changes=changes, request=request)
    await session.commit()
    await session.refresh(faq)     # reload the recomputed search_vector etc.
    return faq


@router.delete("/faqs/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(faq_id: uuid.UUID, admin: AdminOnly, session: SessionDep, request: Request):
    faq = await session.get(BusinessFAQ, faq_id)
    if faq is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found")
    await record_audit(session, action="faq.deleted", entity_type="faq", entity_id=faq.id,
                       actor=admin, request=request, changes={"question": faq.question})
    await session.delete(faq)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------- Services -----------------------------
@router.get("/services", response_model=list[ServiceOut])
async def list_services(_: AnyRole, session: SessionDep, active: bool | None = None):
    stmt = select(Service).order_by(Service.name)
    if active is not None:
        stmt = stmt.where(Service.is_active == active)
    return (await session.scalars(stmt)).all()


@router.post("/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(body: ServiceCreate, admin: AdminOnly, session: SessionDep, request: Request):
    if await session.scalar(select(Service.id).where(func.lower(Service.name) == body.name.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "A service with this name already exists")
    service = Service(**body.model_dump())
    session.add(service)
    await session.flush()
    await record_audit(session, action="service.created", entity_type="service",
                       entity_id=service.id, actor=admin, request=request,
                       changes={k: to_jsonable(v) for k, v in body.model_dump().items()})
    await session.commit()
    await session.refresh(service)
    return service


@router.patch("/services/{service_id}", response_model=ServiceOut)
async def update_service(service_id: uuid.UUID, body: ServiceUpdate, admin: AdminOnly,
                         session: SessionDep, request: Request):
    service = await session.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    data = body.model_dump(exclude_unset=True)
    data = {k: v for k, v in data.items() if v is not None or k in ("price_from", "price_to")}
    # Validate the price range on the COMBINED result (old values + new values).
    new_from = data.get("price_from", service.price_from)
    new_to = data.get("price_to", service.price_to)
    if new_from is not None and new_to is not None and new_to < new_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "price_to must be >= price_from")
    changes = apply_changes(service, data)
    if changes:
        await record_audit(session, action="service.updated", entity_type="service",
                           entity_id=service.id, actor=admin, changes=changes, request=request)
    await session.commit()
    await session.refresh(service)
    return service
