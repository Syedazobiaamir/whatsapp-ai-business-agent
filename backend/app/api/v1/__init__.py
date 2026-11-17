"""Version 1 of the API. A future breaking change would go in /api/v2 without breaking v1 clients."""
from fastapi import APIRouter

from app.api.v1 import (
    agent,
    auth,
    conversations,
    customers,
    dev,
    followups,
    handoffs,
    internal,
    knowledge,
    leads,
    monitoring,
    settings,
    stats,
    team,
    webhooks,
)

api_router = APIRouter()
for module in (auth, team, customers, leads, conversations, handoffs, knowledge, settings, stats,
               followups, monitoring, webhooks, agent, internal, dev):
    api_router.include_router(module.router)
