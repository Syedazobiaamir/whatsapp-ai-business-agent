"""AI agent endpoints.

POST /agent/process  - n8n Workflow 1 calls this for each new inbound message (internal API key).
GET  /agent/status   - dashboard Settings page: is Gemini / WhatsApp configured? (no secrets shown)
"""
from fastapi import APIRouter, HTTPException, status

from app.ai.orchestrator import MessageNotFound, Orchestrator, ProcessingInProgress
from app.api.deps import AnyRole, InternalOnly, LLMFactoryDep, SessionDep, SettingsDep, WhatsAppDep
from app.models import AppSetting
from app.schemas.agent import AgentDecision, ProcessRequest

router = APIRouter(prefix="/agent", tags=["ai agent"])


@router.post("/process", response_model=AgentDecision, dependencies=[InternalOnly])
async def process_message(body: ProcessRequest, session: SessionDep, llm_factory: LLMFactoryDep,
                          wa: WhatsAppDep):
    try:
        return await Orchestrator(session, llm_factory, wa).process(body.message_id, send=body.send)
    except MessageNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found") from None
    except ProcessingInProgress:
        # Another request is working on this message right now: the caller should NOT resend.
        raise HTTPException(status.HTTP_409_CONFLICT, "Message is already being processed") from None


@router.get("/status")
async def agent_status(_: AnyRole, settings: SettingsDep, session: SessionDep):
    ai = await session.get(AppSetting, "ai_config")
    return {
        "gemini_configured": bool(settings.gemini_api_key.get_secret_value()),
        "gemini_model": (ai.value.get("model") if ai else None) or settings.gemini_model,
        "whatsapp_mock_mode": settings.wa_mock_mode,
        "whatsapp_env": settings.wa_env,
        "whatsapp_configured": bool(settings.wa_phone_number_id and settings.wa_access_token.get_secret_value()),
        "webhook_signature_check": bool(settings.wa_app_secret.get_secret_value()),
        "graph_api_version": settings.wa_graph_api_version,
    }
