"""Password hashing and JWT access tokens."""
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

# Used when the email does not exist, so a login attempt takes the same time either way.
# Otherwise an attacker could measure response times to discover which emails are registered.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def hash_password(plain: str) -> str:
    """bcrypt adds a random salt, so the same password gives a different hash each time."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if hashed is None:
        bcrypt.checkpw(plain.encode("utf-8"), _DUMMY_HASH.encode())  # burn the same time
        return False
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(agent_id: uuid.UUID, expires_minutes: int | None = None) -> str:
    """A signed token that proves "this request comes from agent X" until it expires."""
    settings = get_settings()
    now = datetime.now(UTC)
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    payload = {
        "sub": str(agent_id),                       # subject = who the token belongs to
        "iat": now,                                 # issued at
        "exp": now + timedelta(minutes=minutes),    # expiry: stolen tokens stop working
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


class InvalidTokenError(Exception):
    pass


def decode_access_token(token: str) -> uuid.UUID:
    """Return the agent id inside a valid token, or raise InvalidTokenError."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],   # never trust the algorithm named in the token
            options={"require": ["exp", "sub"]},
        )
        if payload.get("type") != "access":
            raise InvalidTokenError("wrong token type")
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError) as exc:     # bad signature, expired, malformed...
        raise InvalidTokenError(str(exc)) from exc
