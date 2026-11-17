# Loom demo script (about 6-7 minutes)

**Audience:** a company founder. **Goal:** show business value first, then prove it is solid engineering.

## Before you record (10 minutes)
```powershell
docker compose --profile full up -d                  # or the dev setup from SETUP.md
backend\.venv\Scripts\python scripts\demo_data.py    # Hamza (HOT), Ayesha (WARM + follow-up), Usman (COLD), Sana (handoff)
```
- Open tabs: **dashboard** (http://127.0.0.1:8080, logged in as admin) · **n8n** (http://127.0.0.1:5690 → WA 1 open) · **PowerShell** in the project folder · **GitHub repo/README**.
- Browser zoom 110%, close other tabs and notifications, hide the bookmarks bar.
- Practise the terminal commands once. Have `.\scripts\send-test-webhook.ps1` ready.
- If you added a Gemini key, run `.\scripts\try-ai.ps1 -Text "I need an online store, budget 1500 USD, start in 2 weeks"` once beforehand to check it works.

---

### 0:00-0:30 · The problem (camera on, Overview page)
> "Small businesses get most leads on WhatsApp, but nobody answers at 11 pm, and nobody follows up. I built an AI agent that answers customers, qualifies leads, hands difficult chats to the team, and recovers from its own errors. Let me show you."

### 0:30-1:30 · Overview → a hot lead
Show the **Overview** tiles, then **Leads → Hamza Siddiqui**.
> "Every WhatsApp conversation becomes a lead. Hamza wants an online store, has 2,000 dollars and wants to start in two weeks, so he's **HOT, 100 out of 100**. Look at the reason: the score comes from transparent rules, not a black box. The AI only extracts the facts. Whenever a lead turns hot, workflow 2 emails the sales team, exactly once."

Point at the **activity timeline** and the **Update lead** panel (change status to *Proposal*, save).

### 1:30-2:45 · Live message through the whole pipeline
Terminal:
```powershell
.\scripts\send-test-webhook.ps1 -Url http://127.0.0.1:5690/webhook/whatsapp -WaId 923001239999 -Name "Live Demo" -Text "Do you provide support after launch?" -MessageId "wamid.LOOM.1"
```
(The fixed `-MessageId` matters: without it the script invents a new id every run, and the duplicate demo below would not be a duplicate.)
Switch to **n8n → WA 1 → Executions**, open the newest run.
> "That's exactly what Meta sends, with a cryptographic signature. n8n forwards the raw bytes, the backend verifies the signature, saves the message, answers Meta in milliseconds, then the AI replies."

Dashboard → **Conversations → Live Demo**: show the reply and its badges (intent, modules, confidence or *fallback*).
Run the **exact same command again** (same `-MessageId`); the terminal shows `"duplicates": 1`.
> "Meta sometimes delivers the same message twice. The second time nothing happens: the database itself guarantees one reply per message."

### 2:45-3:45 · Human handoff
Conversations → **Sana Malik** (complaint, *Handoff open*, *Human*).
> "When a customer complains or asks for a person, the AI stops and the least-busy agent is notified."

Type a reply ("Hi Sana, I'm sorry. I'm checking your order now.") → **Send** → **Resume AI**.
> "The team replies from here. When they're done, the AI takes over again."

### 3:45-4:30 · Follow-ups with consent
**Automation → Follow-up queue → Ayesha Khan → Approve.**
> "Ayesha went quiet for a day. The system planned a follow-up, but only because she agreed to marketing messages, and only with a WhatsApp-approved template because the 24-hour window is closed. Nothing goes out until a person approves it."

### 4:30-5:30 · Self-healing errors
Terminal: `curl.exe -X POST http://127.0.0.1:5690/webhook/error-drill`
Dashboard → **Automation errors** (refresh): show the new error: *temporary · risky · needs approval · alert sent*. Run the drill again → **×2**, no second alert.
> "Failures are grouped and classified. Safe ones retry themselves. If a retry could send a customer a duplicate message, an admin has to approve it, and that approval is audited. The system never repeats anything blindly."

### 5:30-6:30 · Under the hood (README / docs)
Show the **architecture diagram**, **n8n screenshots**, and **TEST_RESULTS.md**.
> "FastAPI with PostgreSQL holds every business rule. n8n orchestrates six workflows. Gemini returns structured JSON validated by Pydantic. It has 197 backend tests at 94% coverage, frontend tests, and an end-to-end test that runs against the real stack, including inside Docker. That end-to-end test found a real PostgreSQL bug that unit tests couldn't. Production runs with Docker Compose behind Caddy with automatic HTTPS."

### 6:30-7:00 · Close
> "It's ready to connect to a real WhatsApp Business number and a Gemini key, and every step for that is documented. I'd love to adapt it to your business. Thanks for watching!"

---
## Backup plans
| If… | Do |
|---|---|
| No Gemini key | Say "the AI is running in fallback mode: answers come from the FAQ search", which is a feature in itself. The Automation page will also list **"GEMINI_API_KEY is not set"** (one grouped error): point at it, "even a missing AI key is reported, never silent" |
| Re-recording a segment | Before re-running the live message, delete it: `docker exec wa-agent-postgres psql -U wa_agent -d wa_agent -c "DELETE FROM customers WHERE wa_id='923001239999';"` (or simply rerun `scripts\demo_data.py` and use a new `-MessageId`) |
| A command fails live | Cut and re-record that segment; Loom lets you trim |
| n8n asks for login | Use `N8N_OWNER_EMAIL` / `N8N_OWNER_PASSWORD` from `.env` (local only) |
| You want to reset | `backend\.venv\Scripts\python scripts\demo_data.py` (recreates the demo customers) |
