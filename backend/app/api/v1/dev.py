"""Development helpers: simulate a customer message without a phone.

Only available while WA_MOCK_MODE=true and APP_ENV is not production (404 otherwise).
It builds the same JSON Meta would send and runs it through the real processing code.
"""
import time
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.orchestrator import Orchestrator
from app.api.deps import AdminOnly, LLMFactoryDep, SessionDep, SettingsDep, WhatsAppDep
from app.services.messaging import process_webhook
from app.services.whatsapp import WAWebhook

router = APIRouter(prefix="/dev", tags=["development"])


class SimulateInbound(BaseModel):
    wa_id: str = Field(default="923001234567", pattern=r"^\d{8,15}$")
    name: str = Field(default="Test Customer", max_length=120)
    text: str = Field(min_length=1, max_length=4096)
    wa_message_id: str | None = Field(default=None, description="Reuse an id to test duplicates")
    auto_reply: bool = Field(default=True, description="Run the AI agent right away (like n8n would)")


def build_meta_payload(wa_id: str, name: str, text: str, wa_message_id: str,
                       phone_number_id: str = "MOCK_PHONE_NUMBER_ID") -> dict:
    """The exact shape of Meta's 'incoming text message' webhook."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "MOCK_WABA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15550000000",
                                 "phone_number_id": phone_number_id},
                    "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
                    "messages": [{"from": wa_id, "id": wa_message_id,
                                  "timestamp": str(int(time.time())),
                                  "type": "text", "text": {"body": text}}],
                },
            }],
        }],
    }


@router.post("/simulate-inbound")
async def simulate_inbound(body: SimulateInbound, _: AdminOnly, session: SessionDep,
                           settings: SettingsDep, llm_factory: LLMFactoryDep, wa: WhatsAppDep):
    if not settings.wa_mock_mode or settings.app_env == "production":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    wamid = body.wa_message_id or f"wamid.SIM.{uuid.uuid4().hex}"
    payload = build_meta_payload(body.wa_id, body.name, body.text, wamid,
                                 settings.wa_phone_number_id or "MOCK_PHONE_NUMBER_ID")
    result = await process_webhook(session, WAWebhook.model_validate(payload), settings)
    decisions = []
    if body.auto_reply:
        orchestrator = Orchestrator(session, llm_factory, wa)
        decisions = [await orchestrator.process(mid) for mid in result.new_message_ids]
    return {"wa_message_id": wamid,
            "new_message_ids": [str(i) for i in result.new_message_ids],
            "duplicates": result.duplicates,
            "decisions": [d.model_dump(mode="json") for d in decisions]}
