"""Team management: human agents and their roles."""
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import AdminOnly, AnyRole, SessionDep
from app.core.security import hash_password
from app.models import HumanAgent
from app.models.enums import AgentRole
from app.schemas.team import AgentCreate, AgentOut, AgentUpdate
from app.services.audit import apply_changes, record_audit

router = APIRouter(prefix="/team", tags=["team"])


@router.get("", response_model=list[AgentOut])
async def list_team(_: AnyRole, session: SessionDep):
    """All roles can see the team (needed for the 'assign to' dropdowns)."""
    return (await session.scalars(select(HumanAgent).order_by(HumanAgent.full_name))).all()


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, admin: AdminOnly, session: SessionDep, request: Request):
    email = body.email.lower()
    if await session.scalar(select(HumanAgent.id).where(func.lower(HumanAgent.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A team member with this email already exists")
    agent = HumanAgent(
        full_name=body.full_name, email=email, phone=body.phone, role=body.role,
        password_hash=hash_password(body.password),
    )
    session.add(agent)
    await session.flush()
    await record_audit(session, action="team.created", entity_type="human_agent",
                       entity_id=agent.id, actor=admin, request=request,
                       changes={"email": email, "role": body.role.value})
    await session.commit()
    await session.refresh(agent)
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdate, admin: AdminOnly, session: SessionDep, request: Request
):
    agent = await session.get(HumanAgent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team member not found")

    # exclude_unset: only fields the client actually sent. exclude_none: ignore "null"s,
    # because role / is_active / full_name may never be empty in the database.
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    if agent.id == admin.id and (
        data.get("is_active") is False or data.get("role", AgentRole.ADMIN) != AgentRole.ADMIN
    ):
        # Prevents the last admin from locking everyone out by accident.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove your own admin access")

    password = data.pop("password", None)
    changes = apply_changes(agent, data)
    if password:
        agent.password_hash = hash_password(password)
        changes["password"] = "changed"
    if changes:
        await record_audit(session, action="team.updated", entity_type="human_agent",
                           entity_id=agent.id, actor=admin, changes=changes, request=request)
    await session.commit()
    await session.refresh(agent)
    return agent
