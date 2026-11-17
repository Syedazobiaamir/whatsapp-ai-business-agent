"""Request/response of POST /agent/process (called by n8n Workflow 1)."""
import uuid
from typing import Literal

from pydantic import BaseModel


class ProcessRequest(BaseModel):
    message_id: uuid.UUID                 # an inbound message id returned by the webhook
    send: bool = True                     # False = create the reply as 'pending', caller sends it


class LeadSummary(BaseModel):
    id: uuid.UUID
    temperature: str
    score: int
    reason: str | None
    became_hot: bool                       # just turned HOT -> notify sales (Workflow 2)


class AgentDecision(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    customer_wa_id: str
    action: Literal["replied", "handoff", "skipped"]
    skip_reason: str | None = None         # human_mode | not_inbound
    intent: str | None = None
    confidence: float | None = None
    reply_message_id: uuid.UUID | None = None
    reply_text: str | None = None
    reply_send_status: str | None = None
    handoff_request_id: uuid.UUID | None = None
    handoff_reason: str | None = None
    assigned_agent_id: uuid.UUID | None = None
    lead: LeadSummary | None = None
    notify_sales: bool = False
    fallback_used: bool = False            # Gemini unavailable -> FAQ search / handoff used
    guardrails: list[str] = []             # e.g. ["unapproved_amount: $99"]
    modules: list[str] = []                # which modules ran, for transparency/debugging
    already_processed: bool = False        # a retry: the stored decision is returned, nothing resent
