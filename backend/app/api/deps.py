"""Reusable dependencies: database session, current user, role checks.

FastAPI runs a dependency before the endpoint. If it raises an HTTPException,
the endpoint never runs: this is how routes are protected.
"""
import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMClient, build_llm
from app.config import Settings, get_settings
from app.core.security import InvalidTokenError, decode_access_token
from app.db import get_session
from app.models import HumanAgent
from app.models.enums import AgentRole
from app.services.whatsapp import WhatsAppClient

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_whatsapp_client(settings: SettingsDep) -> WhatsAppClient:
    """Tests replace this dependency with a client that uses a fake network."""
    return WhatsAppClient(settings)


WhatsAppDep = Annotated[WhatsAppClient, Depends(get_whatsapp_client)]


def get_llm_factory(settings: SettingsDep) -> Callable[[str | None], LLMClient]:
    """Returns 'make me an LLM for model X'. Tests replace this with a fake Gemini."""
    return lambda model: build_llm(settings, model)


LLMFactoryDep = Annotated[Callable[[str | None], LLMClient], Depends(get_llm_factory)]

# Reads the "Authorization: Bearer <token>" header. tokenUrl powers the /docs "Authorize" button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_agent(
    token: Annotated[str | None, Depends(oauth2_scheme)], session: SessionDep
) -> HumanAgent:
    if not token:
        raise _UNAUTHORIZED
    try:
        agent_id = decode_access_token(token)
    except InvalidTokenError:
        raise _UNAUTHORIZED from None
    # Always re-read the user from the DB: a deactivated user or changed role
    # takes effect immediately, even if their token has not expired yet.
    agent = await session.get(HumanAgent, agent_id)
    if agent is None or not agent.is_active:
        raise _UNAUTHORIZED
    return agent


CurrentAgent = Annotated[HumanAgent, Depends(get_current_agent)]


def require_roles(*roles: AgentRole):
    """Build a dependency that only lets the given roles through (403 otherwise)."""

    async def checker(agent: CurrentAgent) -> HumanAgent:
        if agent.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is not allowed to do this")
        return agent

    return checker


# Ready-made role groups used in the route signatures:
AnyRole = CurrentAgent                                                     # admin, sales, viewer
StaffOnly = Annotated[HumanAgent, Depends(require_roles(AgentRole.ADMIN, AgentRole.SALES_AGENT))]
AdminOnly = Annotated[HumanAgent, Depends(require_roles(AgentRole.ADMIN))]


async def require_internal_key(
    settings: SettingsDep,
    x_internal_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """For machine-to-machine calls (n8n -> backend): a shared secret in a header."""
    expected = settings.backend_internal_api_key.get_secret_value()
    if not expected or not x_internal_api_key or not hmac.compare_digest(x_internal_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API key")


InternalOnly = Depends(require_internal_key)


class Pagination:
    """?page=1&size=20 query parameters, with safe limits."""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        self.page = page
        self.size = size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


PageParams = Annotated[Pagination, Depends()]
