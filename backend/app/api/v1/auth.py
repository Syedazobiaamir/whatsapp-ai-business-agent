"""Login and "who am I"."""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select

from app.api.deps import CurrentAgent, SessionDep
from app.config import get_settings
from app.core.security import create_access_token, verify_password
from app.models import HumanAgent
from app.schemas.team import AgentOut, LoginRequest, TokenResponse
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Same message for "unknown email" and "wrong password": don't tell attackers which one.
_BAD_LOGIN = HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")


async def _login(session: SessionDep, request: Request, email: str, password: str) -> TokenResponse:
    agent = await session.scalar(
        select(HumanAgent).where(func.lower(HumanAgent.email) == email.strip().lower())
    )
    if not verify_password(password, agent.password_hash if agent else None):
        await record_audit(session, action="auth.login_failed", entity_type="auth",
                           changes={"email": email[:255]}, request=request)
        await session.commit()
        raise _BAD_LOGIN
    if not agent.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")

    agent.last_login_at = datetime.now(UTC)
    await record_audit(session, action="auth.login", entity_type="human_agent",
                       entity_id=agent.id, actor=agent, request=request)
    await session.commit()
    minutes = get_settings().jwt_expire_minutes
    return TokenResponse(access_token=create_access_token(agent.id), expires_in=minutes * 60)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, session: SessionDep):
    """Dashboard login with JSON: {"email": "...", "password": "..."}."""
    return await _login(session, request, body.email, body.password)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
async def login_form(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], request: Request, session: SessionDep
):
    """Same login, but as a form: used by the "Authorize" button on /docs."""
    return await _login(session, request, form.username, form.password)


@router.get("/me", response_model=AgentOut)
async def me(agent: CurrentAgent):
    return agent
