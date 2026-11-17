"""Audit trail + helpers for recording what changed."""
import enum
import ipaddress
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, HumanAgent
from app.models.enums import ActorType

SECRET_FIELDS = {"password", "password_hash"}


def to_jsonable(value: Any) -> Any:
    """Turn enums, UUIDs, dates and decimals into plain JSON values."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def apply_changes(obj: Any, data: dict[str, Any]) -> dict[str, list[Any]]:
    """Set attributes on a model and return {"field": [old, new]} for what really changed."""
    changes: dict[str, list[Any]] = {}
    for field, new in data.items():
        old = getattr(obj, field)
        if old != new:
            setattr(obj, field, new)
            changes[field] = [to_jsonable(old), to_jsonable(new)]
    return changes


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    try:
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    actor: HumanAgent | None = None,
    actor_type: ActorType | None = None,
    changes: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Add an audit row to the current transaction (saved when the caller commits)."""
    if changes:
        changes = {k: ("***" if k in SECRET_FIELDS else v) for k, v in changes.items()}
    session.add(
        AuditLog(
            actor_type=actor_type or (ActorType.AGENT if actor else ActorType.SYSTEM),
            actor_agent_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            changes=changes or None,
            ip_address=_client_ip(request),
        )
    )


async def paginate(session: AsyncSession, stmt: Select, page: int, size: int) -> tuple[list, int]:
    """Run a query for one page and also count all matching rows."""
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (await session.scalars(stmt.offset((page - 1) * size).limit(size))).all()
    return list(rows), total or 0
