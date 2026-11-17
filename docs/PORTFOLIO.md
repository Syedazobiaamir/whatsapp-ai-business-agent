# Portfolio description

## One line
An AI WhatsApp sales and support agent for small businesses: it answers customers 24/7, qualifies leads as Hot / Warm / Cold, hands complex chats to people, and recovers from its own failures, without ever sending a message twice.

## Short (GitHub "About" / LinkedIn headline, ~300 characters)
WhatsApp AI Business Agent: FastAPI + PostgreSQL + Gemini + n8n + React. Answers FAQs, qualifies leads with explainable rules, human handoff, consent-aware follow-ups, self-healing error recovery. 224 backend tests (94% coverage), end-to-end tested in Docker.

## Project description (LinkedIn project / portfolio site)
Small businesses lose leads on WhatsApp because nobody answers at night and nobody follows up. I built a complete automation system, not just a chatbot:

- **Customers get instant answers** from the business's own FAQ and price list. Gemini handles the language, and guardrails stop it from inventing prices.
- **Every conversation becomes a qualified lead.** The AI extracts needs, budget and timeline; transparent rules score the lead Hot / Warm / Cold and explain why, and sales is emailed once when a lead turns hot.
- **People stay in control.** Complaints or "talk to a human" pause the AI, assign the least-busy agent, and let them reply from the dashboard.
- **Follow-ups respect the rules:** marketing consent, WhatsApp's 24-hour window and templates, and human approval before sending.
- **Speaks like the customers do:** English, Urdu (right-to-left) and Roman Urdu are detected automatically; fixed replies, FAQ answers and the price guardrail work in all three.
- **It recovers from failures safely.** Errors are grouped and classified. Safe ones retry themselves; risky ones (that could duplicate a customer message) wait for an admin's approval.

**Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy (async), Alembic, PostgreSQL · Google Gemini (structured JSON output) · n8n (6 workflows) · WhatsApp Cloud API · React 19, TypeScript, Tailwind, TanStack Query · Docker Compose, nginx, Caddy (HTTPS).

**Quality:** 224 backend tests (94% coverage), 18 frontend tests, a 13-step end-to-end test that runs against the real n8n + backend + database (local and Docker), and deliberate-bug checks proving the tests catch broken rules.

## CV bullets
- Built an AI WhatsApp business agent (FastAPI, PostgreSQL, Gemini, n8n, React) that answers customers, qualifies leads Hot/Warm/Cold with explainable rules, and hands complex chats to human agents.
- Designed idempotent message processing (unique keys, atomic claims, `ON CONFLICT`) so webhook retries and parallel requests never produce duplicate replies; proven with concurrency tests (10 parallel webhooks → 1 message).
- Implemented safe automatic recovery: errors are classified and grouped; only failures proven to have had no effect are retried, and risky retries need admin approval with an audit trail.
- Found and fixed a PostgreSQL generic-plan bug (partial-index `ON CONFLICT`) through end-to-end testing; verified the fix by re-running the full suite with generic plans forced.
- Containerised the stack (non-root images, nginx with CSP, Caddy HTTPS); end-to-end suite passes 13/13 inside Docker; tests pass on PostgreSQL 16 and 17.

## Likely interview questions, with short answers
| Question | Answer |
|---|---|
| Why n8n *and* FastAPI? | n8n is good at triggers, schedules and notifications, and a business can see and adjust it. Business rules need types and tests, so they live in FastAPI. Every endpoint n8n calls is idempotent, which makes n8n's retries safe. |
| How do you stop duplicate replies? | Three layers: a unique `wa_message_id` with `ON CONFLICT DO NOTHING`; an atomic claim before the AI runs; a send endpoint that only sends a `pending` message once. A test with 5 parallel calls proves there is 1 reply. |
| Can a customer trick the AI? | Customer text is treated as data in the prompt. Output must match a Pydantic schema. Prices are checked against the catalog. The lead score comes from rules, not from the model. |
| What if Gemini is down? | It falls back to PostgreSQL full-text search over the FAQ; if nothing matches, a human takes over. The outage is grouped into one error and one alert. |
| What if WhatsApp times out? | A timeout after sending means the message *may* have arrived, so it is never retried automatically. It goes to `needs_approval` and an admin decides. Connection refused or an explicit rate limit means it was not sent, so it is safe to retry with backoff. |
| Hardest bug? | Inserts worked 5 times and then failed. PostgreSQL switches prepared statements to generic plans, and a parameterised `ON CONFLICT … WHERE` could no longer match the partial index. I reproduced it deterministically with `plan_cache_mode=force_generic_plan` and fixed it with a literal predicate. |
| What would you add next? | Real Meta and Gemini go-live, pgvector semantic search for larger FAQs, multi-language templates, a queue (e.g. Redis) for very high volume, and metrics/tracing (Prometheus/OpenTelemetry). |

## Honest scope
Built and tested in mock mode: WhatsApp sending is simulated, and Gemini is exercised through a fake that follows the real SDK interface. Automatic retries through the real n8n API were verified live. Live Meta delivery, live Gemini answers and Gmail sending need accounts (free test tiers are documented). They are listed as "not tested" rather than claimed.
