"""Stage 4 tests: authentication, tokens and role-based access control."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.models import AuditLog
from app.models.enums import AgentRole


async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["database"] == "ok"


async def test_login_success_returns_token(client, make_agent):
    await make_agent(AgentRole.ADMIN, email="boss@test.dev", password="Correct-Horse-1")
    r = await client.post("/api/v1/auth/login",
                          json={"email": "BOSS@test.dev", "password": "Correct-Horse-1"})
    assert r.status_code == 200, r.text            # email is case-insensitive
    token = r.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "boss@test.dev"
    assert "password_hash" not in me.json()        # never exposed


async def test_wrong_password_and_unknown_email_look_identical(client, make_agent, session):
    await make_agent(AgentRole.ADMIN, email="boss@test.dev")
    wrong_pw = await client.post("/api/v1/auth/login", json={"email": "boss@test.dev", "password": "nope"})
    no_user = await client.post("/api/v1/auth/login", json={"email": "ghost@test.dev", "password": "nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()       # attacker learns nothing
    failed = (await session.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed"))).all()
    assert len(failed) == 2                        # failed attempts are recorded


async def test_disabled_account_cannot_log_in(client, make_agent, session):
    agent, headers = await make_agent(AgentRole.SALES_AGENT, email="old@test.dev")
    agent.is_active = False
    await session.commit()
    r = await client.post("/api/v1/auth/login", json={"email": "old@test.dev", "password": "Correct-Horse-1"})
    assert r.status_code == 403
    # An existing token stops working immediately too:
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.parametrize("header", [
    None,                                         # no token
    "Bearer not-a-jwt",                           # garbage
    "Bearer " + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.forged",   # forged signature
])
async def test_protected_route_rejects_bad_tokens(client, header):
    headers = {"Authorization": header} if header else {}
    assert (await client.get("/api/v1/leads", headers=headers)).status_code == 401


async def test_expired_token_is_rejected(client, make_agent):
    agent, _ = await make_agent(AgentRole.ADMIN)
    expired = create_access_token(agent.id, expires_minutes=-1)
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


# --- Role-based access control: (role, method, url, json, expected status) ---
RBAC_CASES = [
    (AgentRole.VIEWER, "get", "/api/v1/leads", None, 200),
    (AgentRole.VIEWER, "post", "/api/v1/faqs", {"question": "Is this allowed?", "answer": "No"}, 403),
    (AgentRole.VIEWER, "get", "/api/v1/audit-logs", None, 403),
    (AgentRole.SALES_AGENT, "post", "/api/v1/faqs", {"question": "Is this allowed?", "answer": "No"}, 403),
    (AgentRole.SALES_AGENT, "post", "/api/v1/team",
     {"full_name": "New Person", "email": "n@test.dev", "password": "Long-Password-1"}, 403),
    (AgentRole.SALES_AGENT, "put", "/api/v1/settings/ai_config", {}, 403),
    (AgentRole.ADMIN, "get", "/api/v1/audit-logs", None, 200),
    (AgentRole.ADMIN, "post", "/api/v1/faqs", {"question": "Is this allowed?", "answer": "Yes"}, 201),
]


@pytest.mark.parametrize(("role", "method", "url", "body", "expected"), RBAC_CASES)
async def test_role_permissions(client, make_agent, role, method, url, body, expected):
    _, headers = await make_agent(role)
    kwargs = {"headers": headers} | ({"json": body} if body is not None else {})
    r = await getattr(client, method)(url, **kwargs)
    assert r.status_code == expected, r.text


async def test_admin_manages_team_and_cannot_lock_self_out(client, make_agent):
    admin, headers = await make_agent(AgentRole.ADMIN)
    new = {"full_name": "Sara Sales", "email": "Sara@Test.dev", "password": "Long-Password-1",
           "role": "sales_agent"}
    r = await client.post("/api/v1/team", json=new, headers=headers)
    assert r.status_code == 201 and r.json()["email"] == "sara@test.dev"
    dup = await client.post("/api/v1/team", json=new, headers=headers)
    assert dup.status_code == 409

    weak = await client.post("/api/v1/team", headers=headers,
                             json=new | {"email": "x@test.dev", "password": "short"})
    assert weak.status_code == 422                 # Pydantic validation

    selfdemote = await client.patch(f"/api/v1/team/{admin.id}", json={"role": "viewer"}, headers=headers)
    assert selfdemote.status_code == 400

    # The new member can log in with the password the admin set:
    login = await client.post("/api/v1/auth/login",
                              json={"email": "sara@test.dev", "password": "Long-Password-1"})
    assert login.status_code == 200
