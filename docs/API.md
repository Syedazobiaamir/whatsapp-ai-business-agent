# API reference

_Generated from the FastAPI code by `scripts/generate_docs.py`. Interactive version: `http://127.0.0.1:8000/docs` (development only). Machine-readable: [openapi.json](openapi.json)._

**58 endpoints.** Base path `/api/v1`. Dashboard calls send `Authorization: Bearer <JWT>` from `POST /api/v1/auth/login`; roles are enforced on the server (admin · sales agent · viewer).

## ai agent

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/agent/process` | internal key (n8n) | Process Message |
| `GET` | `/api/v1/agent/status` | JWT | Agent Status |

## auth

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | public | Dashboard login with JSON: {"email": "...", "password": "..."}. |
| `GET` | `/api/v1/auth/me` | JWT | Me |

## conversations

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/conversations` | JWT | List Conversations |
| `GET` | `/api/v1/conversations/{conversation_id}` | JWT | Get Conversation |
| `POST` | `/api/v1/conversations/{conversation_id}/reply` | JWT | A human sends a WhatsApp message. Requires taking over first (AI and human never both talk). |
| `POST` | `/api/v1/conversations/{conversation_id}/resume` | JWT | Hand the conversation back to the AI and close the active handoff. |
| `POST` | `/api/v1/conversations/{conversation_id}/takeover` | JWT | A human takes control: the AI stops replying to this conversation. |

## customers

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/customers` | JWT | List Customers |
| `GET` | `/api/v1/customers/{customer_id}` | JWT | Get Customer |
| `PATCH` | `/api/v1/customers/{customer_id}` | JWT | Update Customer |

## dashboard

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/audit-logs` | JWT | Audit Logs |
| `GET` | `/api/v1/stats/analytics` | JWT | Analytics |
| `GET` | `/api/v1/stats/overview` | JWT | Overview |

## development

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/dev/simulate-inbound` | JWT | Simulate Inbound |

## follow-ups

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/follow-ups` | JWT | List Follow Ups |
| `POST` | `/api/v1/follow-ups/{task_id}/approve` | JWT | Approve |
| `POST` | `/api/v1/follow-ups/{task_id}/cancel` | JWT | Cancel |

## handoffs

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/handoffs` | JWT | List Handoffs |

## health

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | public | Used by Docker and monitoring: is the API up and can it reach the database? |

## internal (n8n)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/internal/automation-errors` | internal key (n8n) | Called by WA 5 (Error Trigger). Classifies, groups, and decides retry / alert / approval. |
| `POST` | `/api/v1/internal/automation-errors/{error_id}/alerted` | internal key (n8n) | Mark Alerted |
| `GET` | `/api/v1/internal/conversations/{conversation_id}/assignee` | internal key (n8n) | Who should hear about a new customer message while a human handles the chat? |
| `GET` | `/api/v1/internal/follow-ups/due` | internal key (n8n) | Follow Ups Due |
| `POST` | `/api/v1/internal/follow-ups/plan` | internal key (n8n) | Follow Ups Plan |
| `POST` | `/api/v1/internal/follow-ups/{task_id}/send` | internal key (n8n) | Follow Up Send |
| `POST` | `/api/v1/internal/handoffs/auto-resume` | internal key (n8n) | Handoffs Auto Resume |
| `POST` | `/api/v1/internal/handoffs/{handoff_id}/assign` | internal key (n8n) | Assign |
| `GET` | `/api/v1/internal/leads/{lead_id}/notification` | internal key (n8n) | Everything the 'hot lead' email needs, plus already_notified to avoid duplicates. |
| `POST` | `/api/v1/internal/leads/{lead_id}/notified` | internal key (n8n) | Mark Lead Notified |
| `POST` | `/api/v1/internal/messages/{message_id}/send` | internal key (n8n) | Send a 'pending' outbound message (the AI reply). Calling it again never re-sends. |
| `POST` | `/api/v1/internal/notifications/log` | internal key (n8n) | 'log' notification channel: used until Gmail is connected, and visible in the audit log. |
| `GET` | `/api/v1/internal/recovery/alerts` | internal key (n8n) | Errors that need a human and have not been announced yet (one alert per error group). |
| `POST` | `/api/v1/internal/recovery/run` | internal key (n8n) | Called every 5 min by WA 5: retries SAFE errors that are due. Never touches risky ones. |

## knowledge base

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/faqs` | JWT | List Faqs |
| `POST` | `/api/v1/faqs` | JWT | Create Faq |
| `DELETE` | `/api/v1/faqs/{faq_id}` | JWT | Delete Faq |
| `PATCH` | `/api/v1/faqs/{faq_id}` | JWT | Update Faq |
| `GET` | `/api/v1/services` | JWT | List Services |
| `POST` | `/api/v1/services` | JWT | Create Service |
| `PATCH` | `/api/v1/services/{service_id}` | JWT | Update Service |

## leads

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/leads` | JWT | List Leads |
| `GET` | `/api/v1/leads/{lead_id}` | JWT | Get Lead |
| `PATCH` | `/api/v1/leads/{lead_id}` | JWT | Update Lead |

## monitoring

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/automation-errors` | JWT | List Errors |
| `POST` | `/api/v1/automation-errors/{error_id}/approve-retry` | JWT | A human explicitly decides to retry. RISKY retries (may duplicate a message) need an admin. |
| `POST` | `/api/v1/automation-errors/{error_id}/ignore` | JWT | Ignore Error |
| `POST` | `/api/v1/automation-errors/{error_id}/resolve` | JWT | Resolve Error |
| `GET` | `/api/v1/monitoring/n8n` | JWT | Workflows (active or not) + the last 20 executions, read from n8n's public REST API. |

## settings

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/settings` | JWT | List Settings |
| `GET` | `/api/v1/settings/{key}` | JWT | Get Setting |
| `PUT` | `/api/v1/settings/{key}` | JWT | Update Setting |

## team

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/team` | JWT | All roles can see the team (needed for the 'assign to' dropdowns). |
| `POST` | `/api/v1/team` | JWT | Create Agent |
| `PATCH` | `/api/v1/team/{agent_id}` | JWT | Update Agent |

## webhooks

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/webhooks/whatsapp` | Meta signature / verify token | Meta calls this once when you save the webhook URL in the Meta dashboard. |
| `POST` | `/api/v1/webhooks/whatsapp` | Meta signature / verify token | Receive Webhook |
