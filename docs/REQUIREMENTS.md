# Requirements → implementation → evidence

✅ built and verified · 🟡 built and verified with mocks/fakes; a live check needs your account (Meta, Gemini, Gmail, server)

## 1. Project objective
| Requirement | Where | Evidence | Status |
|---|---|---|---|
| Receive customer WhatsApp messages | n8n WA 1 → `POST /webhooks/whatsapp` | `test_whatsapp.py`, e2e #2 (local + Docker) | ✅ (🟡 real Meta) |
| Understand questions with Gemini | `app/ai/agents.py`, `llm.py` | `test_ai.py` (fake LLM), `test_gaps.py` (real client code, fake SDK) | 🟡 needs `GEMINI_API_KEY` |
| Answer questions about services | Support module + FAQ full-text search + service catalog | e2e #2, `test_faq_answer_is_sent` | ✅ |
| Capture customer information | Sales module → `LeadFacts` merged into customer/lead | `test_sales_conversation_creates_hot_lead` | ✅ |
| Qualify leads Hot / Warm / Cold | `lead_qualification.py` (transparent rules + reason) | `test_qualification_rules`, demo data | ✅ |
| Store in PostgreSQL | 13 tables, 3 migrations | `test_database.py`, migrations tested up/down | ✅ |
| Manage conversations and leads | Dashboard: Leads, Conversations | browser click-tests | ✅ |
| Notify sales representatives | WA 2 (hot lead) + WA 3 (handoff) → Gmail/log | e2e #5, `test_lead_notification_is_deduplicated` | ✅ (🟡 Gmail login) |
| Transfer complex chats to humans | Handoff (keyword, complaint, low confidence) | e2e #5-6, `test_escalation_cases` | ✅ |
| Permitted automated follow-ups | WA 4 + consent, 24h/template rules, approval | e2e #8, `test_follow_up_eligibility` | ✅ |
| Detect automation errors, handle failures | WA 5 + recovery service | e2e #9, `test_recovery.py`, live drill, **live n8n-API retries** (TEST_RESULTS) | ✅ |
| Professional admin dashboard | React + TS + Tailwind, 6 pages | screenshots, 0 console errors | ✅ |

## 2. Technology stack
React · TypeScript · Tailwind · responsive ✅ · Python · FastAPI · REST · Pydantic ✅ · PostgreSQL · SQLAlchemy · Alembic ✅ · Gemini structured output, context, intent ✅ · FAQ knowledge base ✅ · n8n, webhooks, HTTP Request nodes, Error Trigger, retry logic, Gmail ✅ · Docker, Compose, Git, environment variables, deployment docs ✅

## 3. WhatsApp Cloud API
| Requirement | Where | Status |
|---|---|---|
| Official Cloud API (Graph v27.0, checked in Meta docs) | `services/whatsapp/client.py` | ✅ (🟡 live) |
| Webhook verification | `GET /webhooks/whatsapp` | ✅ |
| Signature validation (HMAC-SHA256, raw bytes) | `signature.py` | ✅ |
| Incoming text, button, list, media | `payload.py` | ✅ |
| Send text + template messages | `send_text`, `send_template` | ✅ |
| Delivery statuses (forward-only) | `apply_status` | ✅ |
| 24-hour window + templates | `within_window`, follow-up rules | ✅ |
| Test number and real business number | `WA_ENV`, [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) | ✅ docs (🟡 live) |
| Access token management (System User token, rotation) | WHATSAPP_SETUP §5 | ✅ docs |
| Production configuration | `APP_ENV=production` refuses mock mode / missing credentials | ✅ |
| No hardcoded credentials | `.env` only; checked before every commit | ✅ |
| No paid services without approval | Mock mode default; free tiers; cost notes in docs | ✅ |

## 4. AI architecture: separated modules, no unnecessary autonomous agents
| Module | Implementation | Status |
|---|---|---|
| A. Customer Support | `SupportAgent`: intent, FAQ answer, escalate | ✅ |
| B. Sales | `SalesAgent`: one qualifying question, needs, services, budget | ✅ |
| C. Lead Qualification | rules → Hot/Warm/Cold + reason, saved + timeline | ✅ |
| D. Follow-up | `services/followups.py`: eligibility, consent, window, templates | ✅ |
| E. Monitoring & Recovery | `services/recovery.py`: classify, safe retry, alert, approval for risky | ✅ |
| Orchestration | Plain deterministic code (`orchestrator.py`) | ✅ |

## 5. n8n workflows (importable JSON in `n8n/workflows/`, screenshots in `docs/screenshots/n8n/`)
WA 1 Incoming message ✅ · WA 2 Lead notification ✅ · WA 3 Human handoff (pause AI, notify, human replies, resume when appropriate) ✅ · WA 4 Follow-up automation ✅ · WA 5 Error handling + error logs ✅ · WA 9 Error drill (demo) ✅

## 6. Database
All 11 required tables plus `services` and `app_settings` · primary keys, foreign keys, timestamps, indexes (including partial unique indexes) · Alembic migrations ✅. See [DATABASE.md](DATABASE.md).

## 7. Dashboard
Overview · Leads · Conversations · Automation Monitoring · Settings · Team Management ✅ · authentication + roles (admin / sales agent / viewer) ✅

## 8. Security
Secrets in env ✅ · no keys in the frontend (n8n key stays server-side, tested) ✅ · webhook signatures ✅ · Meta challenge ✅ · payload validation ✅ · protected admin routes ✅. See [ARCHITECTURE.md §5](ARCHITECTURE.md#5-security).

## 9-10. Structure and tests
Required folder structure ✅ · tests for webhook verification, incoming processing, AI responses, lead qualification, DB operations, duplicate prevention, human handoff, WhatsApp failures, retry logic, authentication, dashboard APIs ✅ · mock mode ✅. **197 backend + 18 frontend + 13 e2e.** See [TEST_RESULTS.md](TEST_RESULTS.md).

## Completion criteria
| Criterion | Proven by |
|---|---|
| A customer message is received and stored | e2e #2 (local + Docker) |
| The AI answers it | e2e #2 (FAQ fallback); Gemini path with fake LLM 🟡 |
| A lead is qualified Hot/Warm/Cold with a reason | unit tests + demo data (real rules) |
| Customer data is stored in PostgreSQL | e2e + DB tests |
| Sales is notified about hot leads | WA 2 tests, dedupe tests 🟡 (live run needs Gemini to produce a Hot lead) |
| Human handoff works | e2e #5-6 |
| An automation error is detected and handled | e2e #9 + live drill |

## Extra (Stage 14): Urdu and Roman Urdu
| Requirement | Where | Evidence | Status |
|---|---|---|---|
| Detect English / Urdu / Roman Urdu | `app/ai/language.py` (rules, no AI) | `test_language.py` (10 cases) | ✅ |
| Replies in the customer's language (handoff, STOP/START, safe price, FAQ fallback) | orchestrator + 8 Urdu/Roman Urdu FAQs | `test_language.py` | ✅ |
| "Talk to a human" in Urdu / Roman Urdu | built-in phrases | `test_handoff_in_customers_language` | ✅ |
| Price guardrail understands Urdu digits, روپے/rupay, hazar/lakh | `guardrails.py` | `test_guardrail_understands_urdu_amounts` | ✅ |
| Right-to-left display on the dashboard | `dir="auto"` | browser check: bubbles `rtl` | ✅ |
| Gemini told the customer's language | prompt line | `test_prompt_tells_gemini_the_language` | 🟡 live needs Gemini key |
