# Test results

Date: 2026-11-16 · Windows 10, Python 3.12, Node 24, PostgreSQL 16 (Docker), n8n 2.11.3

| Suite | What it covers | Result |
|---|---|---|
| **Backend unit + integration** (pytest, real PostgreSQL test DB) | DB constraints & migrations, auth/JWT/RBAC, dashboard APIs, WhatsApp webhook (verify, signature, parsing, dedupe), sending + retry rules, AI orchestration (fake Gemini), real Gemini client code (fake SDK), lead qualification, guardrails, n8n endpoints, follow-ups, handoffs, error recovery, edge cases | **194 passed** |
| **Backend coverage** | `pytest --cov` (greenlet-aware, see `.coveragerc`) | **94 %** of 2,909 statements |
| **Frontend unit** (Vitest) | API client (auth header, 401 sign-out, error formatting), helpers | **18 passed** |
| **Frontend build** | `tsc --noEmit` + `vite build` | pass |
| **UI in a real browser** (headless Edge) | 8 views at 1366 px and 390 px, 0 console errors, 0 failed API calls; click-flows: reply / resume AI / validation message / viewer read-only / error approve-resolve | pass |
| **End-to-end** (running n8n → backend → DB) | 13 checks, run twice | **13/13 passed**, see [TEST_RESULTS_E2E.md](TEST_RESULTS_E2E.md) |

## Continuous integration (GitHub Actions)
Every push runs .github/workflows/ci.yml: backend tests on **PostgreSQL 16 and 17**, frontend tests + build, Docker image builds, compose validation (dev + production), and a check that generated files (n8n workflows, API/DB docs) are up to date. Rehearsed locally in a clean clone without .env: 197 passed, 94 % coverage.

## How we know the tests actually test something
Each critical rule was **deliberately broken** to confirm the suite fails:

| Rule broken on purpose | Tests that failed |
|---|---|
| Webhook signature check removed | 6 |
| WhatsApp client retries every error | 3 |
| AI ignores "human mode" | 1 |
| Price guardrail removed | 1 |
| Risky errors treated as safe | 3 |
| Error grouping removed | 3 |
| Atomic "claim" removed (5 parallel requests → **5 replies**) | 1 |

## Bugs found by testing (and fixed)
| Bug | Found by | Fix |
|---|---|---|
| `ON CONFLICT … WHERE status = $1` fails after PostgreSQL switches to a **generic query plan** (from the 6th new customer on a pooled connection) | **End-to-end test**; invisible to unit tests | Literal predicate `status = 'open'`; regression test forces generic plans; whole suite re-run with `plan_cache_mode=force_generic_plan` (194 pass) |
| Messages sent in the same second shown to the AI in random order | Unit test (Stage 6) | `seq` identity column for stable ordering |
| Validation message lost the field name | Browser click-test (Stage 8) | Error formatter fixed + frontend unit test |
| n8n's friendly error texts not classified | Live error drill (Stage 9) | Patterns + exact messages as tests |
| 74 s test run (`localhost` → IPv6 timeout) | Timing (Stage 3) | `127.0.0.1` → 11 s |
| Coverage under-reported (76 % instead of 94 %) | Suspicious numbers (Stage 10) | `concurrency = thread,greenlet` |

## Urdu and Roman Urdu (Stage 14)
27 tests: language detection (incl. greetings like AOA / Assalam o alaikum), Urdu and Roman Urdu FAQ fallback, handoff phrases and replies in 3 languages, STOP confirmation in the remembered language, Urdu-aware price guardrail. Two bugs found while building it: greetings were not recognised, and the old guardrail read the end of "hours 9" as the amount "rs 9" (fixed + regression test).

## Edge cases & abuse tested
10 identical webhooks **in parallel** → 1 message · 5 parallel AI calls → 1 reply · Urdu / emoji round-trip · 3 MB+ payload → 413 · unsigned or forged webhook → 401, nothing stored · `alg: none` JWT → 401 · SQL-injection / `%` / `<script>` search strings → harmless (parameterised queries) · reply > 4096 chars or blank → 422 · database down → `/health` 503 instead of a crash

## Not tested (needs real accounts)
Real Meta delivery and status webhooks · real Gemini answers (the client code is tested against a fake SDK) · Gmail sending (the `log` channel is tested).

## Verified live against the real n8n API (2026-11-17)
With a least-privilege n8n API key (only `workflow:list/read`, `execution:list/read/retry`):
- The Automation page reads all 6 workflows (active) and the last 20 executions from n8n.
- **Automatic retry through n8n:** the drill failed (execution #85) → needs approval → an admin approved → the backend called `POST /executions/85/retry` → n8n created **retry executions** (`mode=retry`) → each failure was grouped onto the same error (×4) → after 3 retries the error became **OPEN**.
- **Exactly 2 alerts**: "APPROVAL NEEDED" at the start and "ACTION NEEDED (x4)" after giving up; none in between.
- Found during this test: Docker's clock ran **3.7 s ahead** of Windows. The app is unaffected (it writes and compares retry times with the same clock), but tests must not mix the two clocks.

## Run it yourself
```powershell
backend\.venv\Scripts\python -m pytest                 # 194 tests
backend\.venv\Scripts\python -m pytest --cov           # + coverage
cd frontend; npm test; npm run build; cd ..            # frontend
.\scripts\run-e2e.ps1                                   # end-to-end (DB must run; stops nothing of yours)
```
