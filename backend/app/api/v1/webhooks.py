"""Public webhook endpoint that Meta (or n8n forwarding Meta's request) calls.

No JWT here: Meta cannot log in. Security comes from:
  GET  -> the verify token (only you and Meta know it)
  POST -> the X-Hub-Signature-256 HMAC signature (only Meta knows the app secret)
"""
import hmac
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.api.deps import SessionDep, SettingsDep
from app.services.messaging import process_webhook
from app.services.whatsapp import WAWebhook, is_valid_signature

logger = logging.getLogger("app.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_BODY_BYTES = 3 * 1024 * 1024   # Meta's documented max payload is 3 MB


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_webhook(
    settings: SettingsDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge", max_length=200)] = None,
):
    """Meta calls this once when you save the webhook URL in the Meta dashboard."""
    expected = settings.wa_verify_token.get_secret_value()
    if (mode == "subscribe" and expected and token and challenge
            and hmac.compare_digest(token, expected)):
        return PlainTextResponse(challenge)          # echo the challenge = "yes, it's me"
    logger.warning("Webhook verification failed (mode=%s)", mode)
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


@router.post("/whatsapp")
async def receive_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
):
    raw = await request.body()          # the EXACT bytes: the signature is computed over these
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")

    secret = settings.wa_app_secret.get_secret_value()
    if secret:
        if not is_valid_signature(raw, x_hub_signature_256, secret):
            logger.warning("Rejected webhook with invalid signature")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    else:
        # Only possible in development + mock mode (config refuses this in production).
        logger.warning("WA_APP_SECRET not set: webhook signature NOT verified (dev only)")

    try:
        webhook = WAWebhook.model_validate(json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not valid JSON") from None
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            exc.errors(include_url=False, include_context=False, include_input=False)) from None

    if webhook.object != "whatsapp_business_account":
        return {"status": "ignored", "reason": f"unsupported object '{webhook.object}'"}

    # If this raises (e.g. database down) we return 500, Meta retries later,
    # and the duplicate protection makes the retry safe.
    result = await process_webhook(session, webhook, settings)
    return {
        "status": "ok",
        "new_message_ids": [str(i) for i in result.new_message_ids],
        "duplicates": result.duplicates,
        "statuses_applied": result.statuses_applied,
        "ignored": result.ignored,
    }
