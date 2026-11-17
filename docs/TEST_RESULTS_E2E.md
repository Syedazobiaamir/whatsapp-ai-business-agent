# End-to-end test results

Run: 2026-11-16 14:43 UTC · stack: PostgreSQL 16 (Docker), FastAPI, n8n 2.11.3 (6 workflows) · WhatsApp **mock mode** · Gemini **not configured** (FAQ fallback path)

**13/13 checks passed**

| Check | Result | Detail |
|---|---|---|
| 1. Meta verification via n8n echoes challenge | ✅ pass |  |
| wrong verify token rejected | ✅ pass | HTTP 403 |
| 2. Customer question saved + AI reply sent | ✅ pass | reply: Yes. Every project includes 1 month of free support. After t |
| 3. Duplicate webhook: no second message / reply | ✅ pass |  |
| 4. Forged signature rejected (401), nothing stored | ✅ pass | HTTP 401 |
| 5. Handoff: AI paused, agent notified (WA 3) | ✅ pass | notifications: 1 |
| next customer message gets NO AI reply | ✅ pass |  |
| 6. Human reply sent from dashboard | ✅ pass |  |
| Resume AI | ✅ pass |  |
| 7. STOP: opted out + confirmation sent | ✅ pass |  |
| 8. Follow-up planned by WA 4, waiting for approval | ✅ pass |  |
| approved follow-up sent exactly once (template) | ✅ pass | sent 1 |
| 9. Failed workflow -> WA 5: 1 grouped error, x2, alerted, awaiting approval | ✅ pass | rows=1 x2 needs_approval |

Reproduce: `.\scripts\run-e2e.ps1`

_Not covered here (needs real accounts): real Meta delivery, real Gemini answers, Gmail sending._
