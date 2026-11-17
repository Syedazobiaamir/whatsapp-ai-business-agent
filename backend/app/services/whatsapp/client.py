"""Sends messages through the WhatsApp Cloud API (or pretends to, in mock mode).

SAFE RETRY RULE ("never repeat messages blindly"):
  We retry automatically ONLY when we are sure Meta did NOT accept the message:
    - we could not even connect (request never left our machine), or
    - Meta answered with an explicit error saying "rate limited" / "temporarily unavailable".
  If the request was sent but we never got an answer (read timeout, gateway 5xx without a
  Meta error body), the customer MAY already have the message. Retrying could send it twice,
  so we stop and flag it for a human to decide (delivery_unknown=True).
"""
import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.models.enums import ErrorType

logger = logging.getLogger("app.whatsapp")

AUTH_CODES = {0, 190}
RATE_LIMIT_CODES = {4, 80007, 130429, 131048, 131056, 131064}
TEMPORARY_CODES = {2, 131000, 131016, 133004}
OUTSIDE_WINDOW_CODE = 131047


class WhatsAppSendError(Exception):
    def __init__(self, message: str, *, error_type: ErrorType, retryable: bool,
                 delivery_unknown: bool = False, status_code: int | None = None,
                 meta_code: int | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.delivery_unknown = delivery_unknown
        self.status_code = status_code
        self.meta_code = meta_code


@dataclass
class SendResult:
    wa_message_id: str
    mocked: bool
    attempts: int


def classify_error_response(resp: httpx.Response) -> WhatsAppSendError:
    """Turn Meta's error JSON into a WhatsAppSendError with the right retry decision."""
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    code = err.get("code")
    text = err.get("message") or resp.text[:300] or f"HTTP {resp.status_code}"
    msg = f"WhatsApp API error {code or resp.status_code}: {text}"

    if code in AUTH_CODES or resp.status_code == 401:
        return WhatsAppSendError(msg, error_type=ErrorType.AUTH, retryable=False,
                                 status_code=resp.status_code, meta_code=code)
    if code in RATE_LIMIT_CODES or resp.status_code == 429:
        return WhatsAppSendError(msg, error_type=ErrorType.RATE_LIMIT, retryable=True,
                                 status_code=resp.status_code, meta_code=code)
    if code == OUTSIDE_WINDOW_CODE:
        return WhatsAppSendError(msg + " (24h window closed: use a template)",
                                 error_type=ErrorType.VALIDATION, retryable=False,
                                 status_code=resp.status_code, meta_code=code)
    if code in TEMPORARY_CODES:
        # Meta explicitly rejected it -> it was NOT sent -> safe to retry.
        return WhatsAppSendError(msg, error_type=ErrorType.TEMPORARY, retryable=True,
                                 status_code=resp.status_code, meta_code=code)
    if resp.status_code >= 500 and code is None:
        # A 5xx without Meta's error body (e.g. a proxy failed): outcome unknown.
        return WhatsAppSendError(msg, error_type=ErrorType.TEMPORARY, retryable=False,
                                 delivery_unknown=True, status_code=resp.status_code)
    return WhatsAppSendError(msg, error_type=ErrorType.VALIDATION if resp.status_code == 400
                             else ErrorType.UNKNOWN, retryable=False,
                             status_code=resp.status_code, meta_code=code)


class WhatsAppClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        self.settings = settings
        self._transport = transport     # tests inject a fake transport here
        self._sleep = sleep             # tests replace sleep so retries run instantly

    @property
    def messages_url(self) -> str:
        s = self.settings
        return f"{s.wa_graph_base_url}/{s.wa_graph_api_version}/{s.wa_phone_number_id}/messages"

    async def send_text(self, to: str, body: str) -> SendResult:
        return await self._send({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        })

    async def send_template(self, to: str, name: str, language: str | None = None,
                            components: list[dict[str, Any]] | None = None) -> SendResult:
        template: dict[str, Any] = {
            "name": name,
            "language": {"code": language or self.settings.wa_template_language},
        }
        if components:
            template["components"] = components
        return await self._send({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template,
        })

    async def _send(self, payload: dict[str, Any]) -> SendResult:
        if self.settings.wa_mock_mode:
            fake_id = f"wamid.MOCK.{uuid.uuid4().hex}"
            logger.info("[MOCK] would send %s to %s", payload["type"], payload["to"])
            return SendResult(wa_message_id=fake_id, mocked=True, attempts=0)

        headers = {"Authorization": f"Bearer {self.settings.wa_access_token.get_secret_value()}"}
        max_attempts = self.settings.wa_max_send_attempts
        async with httpx.AsyncClient(timeout=self.settings.wa_api_timeout_seconds,
                                     transport=self._transport) as http:
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = await http.post(self.messages_url, json=payload, headers=headers)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                    # Never reached Meta -> the message was definitely not sent -> safe to retry.
                    error = WhatsAppSendError(f"Could not connect to WhatsApp API: {exc!r}",
                                              error_type=ErrorType.TEMPORARY, retryable=True)
                except httpx.TimeoutException as exc:
                    # Sent, but no answer: Meta may have delivered it. Do NOT retry.
                    raise WhatsAppSendError(f"No response from WhatsApp API: {exc!r}",
                                            error_type=ErrorType.TEMPORARY, retryable=False,
                                            delivery_unknown=True) from exc
                except httpx.HTTPError as exc:
                    raise WhatsAppSendError(f"HTTP error talking to WhatsApp API: {exc!r}",
                                            error_type=ErrorType.UNKNOWN, retryable=False,
                                            delivery_unknown=True) from exc
                else:
                    if resp.is_success:
                        try:
                            wamid = resp.json()["messages"][0]["id"]
                        except (ValueError, KeyError, IndexError) as exc:
                            raise WhatsAppSendError(
                                "WhatsApp accepted the request but returned no message id",
                                error_type=ErrorType.UNKNOWN, retryable=False,
                                delivery_unknown=True) from exc
                        return SendResult(wa_message_id=wamid, mocked=False, attempts=attempt)
                    error = classify_error_response(resp)

                if not error.retryable or attempt == max_attempts:
                    raise error
                wait = 2 ** (attempt - 1)          # 1s, 2s, 4s ... (exponential backoff)
                logger.warning("WhatsApp send attempt %s failed (%s), retrying in %ss",
                               attempt, error.error_type, wait)
                await self._sleep(wait)
        raise AssertionError("unreachable")
