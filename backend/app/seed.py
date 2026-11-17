"""Load sample business data + the first admin user.

Run from the backend folder:   python -m app.seed
Safe to run many times: existing rows are updated, never duplicated ("idempotent").
"""
import asyncio
import json
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROJECT_ROOT, get_settings
from app.core.security import hash_password
from app.db import SessionLocal, engine
from app.models import AppSetting, BusinessFAQ, HumanAgent, Service
from app.models.enums import AgentRole

SEED_FILE = PROJECT_ROOT / "database" / "seed" / "business.json"
WEAK_PASSWORDS = {"", "change-me", "password", "admin", "12345678"}


async def seed_admin(session: AsyncSession) -> str:
    settings = get_settings()
    password = settings.admin_password.get_secret_value()
    if password in WEAK_PASSWORDS or len(password) < 10:
        sys.exit("ADMIN_PASSWORD in .env is missing or too weak (min 10 characters). Fix it and re-run.")

    existing = await session.scalar(
        select(HumanAgent).where(HumanAgent.email == settings.admin_email)
    )
    if existing:
        return f"admin {settings.admin_email} already exists (password not changed)"

    session.add(
        HumanAgent(
            full_name="Admin",
            email=settings.admin_email,
            password_hash=hash_password(password),
            role=AgentRole.ADMIN,
        )
    )
    return f"admin {settings.admin_email} created"


async def seed_business(session: AsyncSession, data: dict, update: bool = False) -> list[str]:
    """Default: INSERT only what is missing (ON CONFLICT DO NOTHING), so running the seed on every
    container start never overwrites what an admin edited on the dashboard.
    update=True (python -m app.seed --update): overwrite existing rows with the seed file ("upsert")."""
    report = []

    def conflict(stmt, index, values):
        return (stmt.on_conflict_do_update(index_elements=[index], set_=values) if update
                else stmt.on_conflict_do_nothing(index_elements=[index]))

    for key, value in data["settings"].items():
        stmt = conflict(insert(AppSetting).values(key=key, value=value), AppSetting.key, {"value": value})
        await session.execute(stmt)
    report.append(f"{len(data['settings'])} settings")

    for svc in data["services"]:
        values = {
            "name": svc["name"],
            "description": svc["description"],
            "price_from": Decimal(str(svc["price_from"])),
            "price_to": Decimal(str(svc["price_to"])),
            "currency": svc["currency"],
        }
        await session.execute(conflict(insert(Service).values(**values), Service.name, values))
    report.append(f"{len(data['services'])} services")

    for faq in data["faqs"]:
        await session.execute(conflict(insert(BusinessFAQ).values(**faq), BusinessFAQ.question,
                                       {k: v for k, v in faq.items() if k != "question"}))
    report.append(f"{len(data['faqs'])} FAQs")
    return report


async def main() -> None:
    update = "--update" in sys.argv
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    async with SessionLocal() as session:
        # One transaction: either everything is saved or nothing is.
        async with session.begin():
            admin_msg = await seed_admin(session)
            report = await seed_business(session, data, update=update)
    await engine.dispose()
    print("Seed complete:", admin_msg, "|", ", ".join(report), "| mode:", "update" if update else "insert missing")


if __name__ == "__main__":
    asyncio.run(main())
