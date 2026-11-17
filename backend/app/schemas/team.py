"""Auth + team management schemas."""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import AgentRole
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds


class AgentOut(ORMModel):
    """What the API shows about a team member. Note: NO password_hash field."""
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    role: AgentRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class AgentCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    role: AgentRole = AgentRole.SALES_AGENT
    password: str = Field(min_length=10, max_length=128)


class AgentUpdate(BaseModel):
    """Every field optional: only the fields sent are changed (a "partial update")."""
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    role: AgentRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)
