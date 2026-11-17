"""Error monitoring & recovery ("Monitoring and Recovery Agent" - deterministic rules, no LLM).

Every failure becomes ONE row in automation_errors (repeats are grouped by fingerprint) and
gets exactly one of three outcomes:

  RETRYING        safe + temporary  -> retried automatically with growing waits (1m, 5m, 15m)
  OPEN            needs a fix (expired token, bad config) -> alert a human, retrying won't help
  NEEDS_APPROVAL  risky: a retry could DUPLICATE a customer message or email -> a human decides

Golden rule: we never repeat something blindly. "Safe" means we KNOW the first attempt had no
effect (connection refused, explicit rate-limit rejection) or the retried step is idempotent.
"""
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AutomationError, Conversation, Customer, FollowUpTask, Message
from app.models.enums import ErrorStatus, ErrorType, FollowUpStatus, RiskLevel, SendStatus

logger = logging.getLogger("app.recovery")

UNRESOLVED = (ErrorStatus.OPEN, ErrorStatus.RETRYING, ErrorStatus.NEEDS_APPROVAL)
BACKOFF_SECONDS = (60, 300, 900)                 # 1 min, 5 min, 15 min
# Re-running these workflows only repeats idempotent backend calls -> safe.
# WA 2 / WA 3 send emails: re-running them could send a duplicate -> human approval.
SAFE_RETRY_WORKFLOWS = {"WaAgentIncoming1", "WaAgentFollowUp1"}


def backoff(retry_count: int) -> timedelta:
    return timedelta(seconds=BACKOFF_SECONDS[min(retry_count, len(BACKOFF_SECONDS) - 1)])


def _normalise(message: str) -> str:
    """Remove ids/numbers so 'timeout after 3012ms (exec 77)' and '... 2950ms (exec 78)' group together."""
    m = message.lower()
    m = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", m)       # uuids
    m = re.sub(r"wamid\.[\w.=-]+", "<wamid>", m)
    m = re.sub(r"\d+", "#", m)
    return m[:200]


def fingerprint(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


# n8n rewrites low-level errors into friendly English ("The service refused the connection -
# perhaps it is offline" instead of ECONNREFUSED), so we match both forms. Found in a live test.
_AUTH = ("unauthorized", "forbidden", "invalid api key", "authentication", "access token",
         "authorization failed", "check your credentials")
_RATE = ("rate limit", "too many requests", "receiving too many requests")
_TEMPORARY = ("timeout", "timed out", "econnrefused", "econnreset", "econnaborted", "socket hang up",
              "enotfound", "service unavailable", "bad gateway", "temporarily", "refused the connection",
              "perhaps it is offline", "closed unexpectedly", "dns server returned an error",
              "not able to process your request", "try again later")
_VALIDATION = ("validation", "bad request", "could not be found", "check your parameters")


def classify_message(message: str, http_code: int | None = None) -> ErrorType:
    """Turn an n8n error text / HTTP code into an error type."""
    m = message.lower()
    if http_code in (401, 403) or any(w in m for w in _AUTH):
        return ErrorType.AUTH
    if http_code == 429 or any(w in m for w in _RATE):
        return ErrorType.RATE_LIMIT
    if (http_code is not None and http_code >= 500) or any(w in m for w in _TEMPORARY):
        return ErrorType.TEMPORARY
    if http_code in (400, 404, 409, 422) or any(w in m for w in _VALIDATION):
        return ErrorType.VALIDATION
    return ErrorType.UNKNOWN


def initial_status(risk: RiskLevel, retryable: bool) -> ErrorStatus:
    if risk == RiskLevel.RISKY:
        return ErrorStatus.NEEDS_APPROVAL
    return ErrorStatus.RETRYING if retryable else ErrorStatus.OPEN


async def record_error(
    session: AsyncSession, *, source: str, operation: str, error_type: ErrorType, risk: RiskLevel,
    message: str, retryable: bool, node: str | None = None, workflow_name: str | None = None,
    workflow_id: str | None = None, execution_id: str | None = None,
    payload: dict[str, Any] | None = None, unique_key: str | None = None,
) -> tuple[AutomationError, bool]:
    """Create an error row, or count a repeat on the existing unresolved row. Returns (row, is_new)."""
    now = datetime.now(UTC)
    fp = fingerprint(source, operation, workflow_id, node, error_type.value, _normalise(message), unique_key)
    existing = await session.scalar(select(AutomationError).where(
        AutomationError.fingerprint == fp, AutomationError.status.in_(UNRESOLVED)).with_for_update())
    if existing:
        existing.occurrences += 1
        existing.last_seen_at = now
        if execution_id:
            existing.execution_id = execution_id
        if existing.status == ErrorStatus.RETRYING and existing.retry_count >= existing.max_retries:
            existing.status, existing.alerted_at = ErrorStatus.OPEN, None     # give up -> alert again
        return existing, False

    status = initial_status(risk, retryable)
    row = AutomationError(
        source=source, operation=operation, error_type=error_type, risk_level=risk,
        error_message=message[:2000], node_name=node, workflow_name=workflow_name, workflow_id=workflow_id,
        execution_id=execution_id, payload=payload, status=status, fingerprint=fp, last_seen_at=now,
        next_retry_at=now + backoff(0) if status == ErrorStatus.RETRYING else None,
    )
    try:
        async with session.begin_nested():          # savepoint: a parallel insert may win the race
            session.add(row)
    except IntegrityError:
        existing = await session.scalar(select(AutomationError).where(
            AutomationError.fingerprint == fp, AutomationError.status.in_(UNRESOLVED)))
        existing.occurrences += 1
        existing.last_seen_at = now
        return existing, False
    return row, True


def resolve(row: AutomationError, note: str, agent_id: uuid.UUID | None = None) -> None:
    row.status, row.resolved_at = ErrorStatus.RESOLVED, datetime.now(UTC)
    row.resolution_note, row.resolved_by_agent_id, row.next_retry_at = note[:1000], agent_id, None


# ------------------------------------------------------------------ n8n API
def n8n_transport() -> httpx.AsyncBaseTransport | None:
    return None                                     # tests replace this with a fake n8n


async def retry_n8n_execution(settings: Settings, execution_id: str) -> str:
    """Ask n8n to retry a failed execution (it resumes from the failed node). Returns the new id."""
    key = settings.n8n_api_key.get_secret_value()
    if not key:
        raise RuntimeError("N8N_API_KEY not configured")
    async with httpx.AsyncClient(base_url=settings.n8n_base_url.rstrip("/") + "/api/v1", timeout=15,
                                 headers={"X-N8N-API-KEY": key}, transport=n8n_transport()) as client:
        r = await client.post(f"/executions/{execution_id}/retry", json={"loadWorkflow": True})
    r.raise_for_status()
    return str(r.json().get("id", ""))


# ------------------------------------------------------------------ recovery actions
@dataclass
class RecoveryReport:
    resolved: list[str] = field(default_factory=list)
    rescheduled: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)


async def _retry_send_message(session: AsyncSession, wa, row: AutomationError) -> str:
    """Resend a message that FAILED. Never resends one that was sent/delivered."""
    from app.services.messaging import deliver_message
    from app.services.whatsapp import WhatsAppSendError

    msg = await session.get(Message, uuid.UUID((row.payload or {})["message_id"]))
    if msg is None:
        resolve(row, "Message no longer exists")
        return "resolved"
    if msg.send_status not in (SendStatus.FAILED, SendStatus.PENDING):
        resolve(row, f"Already {msg.send_status.value}: nothing to resend")
        return "resolved"
    conv = await session.get(Conversation, msg.conversation_id)
    customer = await session.get(Customer, conv.customer_id)
    msg.send_status, msg.error_message = SendStatus.PENDING, None
    row.retry_count += 1
    await session.commit()
    try:
        await deliver_message(session, wa, msg, conv, customer)
    except WhatsAppSendError as exc:
        await session.refresh(row)
        if exc.delivery_unknown:
            row.status, row.risk_level, row.alerted_at = ErrorStatus.NEEDS_APPROVAL, RiskLevel.RISKY, None
            return "escalated"
        if exc.retryable and row.retry_count < row.max_retries:
            row.status, row.next_retry_at = ErrorStatus.RETRYING, datetime.now(UTC) + backoff(row.retry_count)
            return "rescheduled"
        row.status, row.next_retry_at, row.alerted_at = ErrorStatus.OPEN, None, None
        return "escalated"
    await session.refresh(row)
    resolve(row, f"Resent successfully (attempt {row.retry_count})")
    return "resolved"


async def _check_follow_up(session: AsyncSession, row: AutomationError) -> str:
    """Follow-ups are retried by Workflow 4 itself; here we only track the outcome."""
    task = await session.get(FollowUpTask, uuid.UUID((row.payload or {})["follow_up_task_id"]))
    if task is None or task.status == FollowUpStatus.SENT:
        resolve(row, "Follow-up was sent on a later attempt" if task else "Task removed")
        return "resolved"
    if task.status == FollowUpStatus.APPROVED:            # Workflow 4 will try again
        row.next_retry_at = datetime.now(UTC) + backoff(row.retry_count)
        return "rescheduled"
    row.status, row.next_retry_at, row.alerted_at = ErrorStatus.OPEN, None, None
    return "escalated"


async def _retry_n8n(session: AsyncSession, settings: Settings, row: AutomationError) -> str:
    p = dict(row.payload or {})
    retried_at = p.get("retried_at")
    if retried_at and row.last_seen_at and row.last_seen_at.isoformat() <= retried_at:
        resolve(row, f"n8n retry {p.get('retry_execution_id')} succeeded (no new failure)")
        return "resolved"
    if row.retry_count >= row.max_retries:
        row.status, row.next_retry_at, row.alerted_at = ErrorStatus.OPEN, None, None
        return "escalated"
    try:
        new_id = await retry_n8n_execution(settings, row.execution_id or "")
    except Exception as exc:  # noqa: BLE001 - n8n down / not configured: a human must look
        row.status, row.next_retry_at, row.alerted_at = ErrorStatus.OPEN, None, None
        row.resolution_note = f"Automatic retry impossible: {exc}"[:1000]
        return "escalated"
    row.retry_count += 1
    p.update({"retried_at": datetime.now(UTC).isoformat(), "retry_execution_id": new_id})
    row.payload = p
    row.next_retry_at = datetime.now(UTC) + backoff(row.retry_count)    # check back later
    return "rescheduled"


async def run_recovery(session: AsyncSession, wa, settings: Settings, limit: int = 20) -> RecoveryReport:
    """Process SAFE errors whose retry time has come. Risky ones are never touched here."""
    report = RecoveryReport()
    due = (await session.scalars(select(AutomationError).where(
        AutomationError.status == ErrorStatus.RETRYING, AutomationError.risk_level == RiskLevel.SAFE,
        AutomationError.next_retry_at <= datetime.now(UTC))
        .order_by(AutomationError.next_retry_at).limit(limit))).all()
    for row in due:
        op = row.operation
        try:
            if op == "send_message":
                outcome = await _retry_send_message(session, wa, row)
            elif op == "follow_up":
                outcome = await _check_follow_up(session, row)
            elif op == "n8n_execution":
                outcome = await _retry_n8n(session, settings, row)
            else:
                row.status, row.next_retry_at = ErrorStatus.OPEN, None
                outcome = "escalated"
        except Exception:  # noqa: BLE001 - one broken row must not stop the others
            logger.exception("Recovery failed for %s", row.id)
            await session.rollback()
            continue
        getattr(report, outcome).append(str(row.id))
        await session.commit()
    return report


def alert_text(row: AutomationError, settings: Settings) -> dict[str, str]:
    action = {ErrorStatus.NEEDS_APPROVAL: "APPROVAL NEEDED - a retry could duplicate a message.",
              ErrorStatus.OPEN: "ACTION NEEDED - automatic retry will not fix this.",
              ErrorStatus.RETRYING: "Retrying automatically."}.get(row.status, "")
    where = " / ".join(x for x in (row.workflow_name, row.node_name) if x) or row.source
    return {
        "to": settings.admin_alert_email,
        "subject": f"[WA Agent] {row.error_type.value} error in {where}"
                   + (f" (x{row.occurrences})" if row.occurrences > 1 else ""),
        "body": (f"{action}\n\nSource: {row.source}\nWhere: {where}\nType: {row.error_type.value} "
                 f"(risk: {row.risk_level.value})\nMessage: {row.error_message[:500]}\n"
                 f"Occurrences: {row.occurrences}\n\n"
                 f"Review: {settings.frontend_url.rstrip('/')}/automation"),
    }
