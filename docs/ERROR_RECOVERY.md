# Error Monitoring & Recovery

**Goal:** no failure goes unnoticed, safe failures heal themselves, and nothing is ever repeated blindly.

## Where errors come from
| Source | Example | Recorded by |
|---|---|---|
| WhatsApp send | rate limit, expired token, timeout | backend (`record_send_failure`) |
| Follow-up send | same as above, for templates | backend |
| Gemini | key missing, quota (429) | backend orchestrator (the customer still gets the FAQ fallback) |
| n8n workflow | a node fails | **WA 5** (Error Trigger), set as error workflow on WA 1–4 and WA 9 |

## One decision per error
```
                ┌─ safe + temporary (rate limit, connection refused) ─► RETRYING ─► auto retry 1m, 5m, 15m
failure ─► classify ─┼─ needs a fix (401 token, bad config) ─────────────► OPEN ─────► alert a human
                └─ risky (delivery unknown / could duplicate email) ──► NEEDS_APPROVAL ─► alert + admin decides
```
* **Safe** means we *know* the first attempt had no effect (the connection was refused, or Meta explicitly rejected the request), or the step is idempotent (every backend endpoint n8n calls is).
* **Risky** means the customer *may* already have the message (timeout after sending), or the workflow sends emails (WA 2 / WA 3). A person must approve, and **only an admin** can approve a risky retry. The approval is written to the audit log.
* **Grouping:** a fingerprint (source + step + type + normalised message) turns repeats into **one row ×N** with **one alert**.
* **Give-up rule:** after `max_retries` (3) a retrying error becomes OPEN and is alerted.
* **Never resend a delivered message:** before a retry the backend re-checks the message status; if a later webhook proved delivery, the error is resolved without sending.
* **n8n retries** use n8n's public API `POST /executions/{id}/retry`, which resumes from the failed node. This needs `N8N_API_KEY`. Without it, the dashboard says honestly "Automatic retry impossible".

## WA 5 - Error Handler & Recovery
* **Error Trigger:** report to the backend → if `alert` → Gmail or log → mark alerted.
* **Every 5 minutes:** `POST /internal/recovery/run` (safe retries that are due) → `GET /internal/recovery/alerts` (anything not yet announced) → notify → mark alerted.

## Dashboard (Automation page)
Each error shows source, type, risk, status, ×occurrences, retries, next retry, whether the alert was sent, and notes.
Buttons: **Retry now** (safe), **Approve retry** (risky, admin only, with confirmation), **Mark resolved**, **Ignore**.

## Demo: error drill
```powershell
curl.exe -X POST http://127.0.0.1:5690/webhook/error-drill
```
**WA 9** calls a closed port → fails → WA 5 records it (temporary, risky because WA 9 is not on the safe list) → an alert is logged → it appears on the Automation page waiting for approval.
