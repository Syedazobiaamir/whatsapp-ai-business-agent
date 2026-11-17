"""End-to-end test of the RUNNING system: Meta-style webhooks -> n8n -> backend -> PostgreSQL.

Not a pytest file: it needs the real stack running. Use .\\scripts\\run-e2e.ps1 (starts everything).
WhatsApp is in mock mode (nothing is really sent). Writes docs/TEST_RESULTS_E2E.md.
"""
import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Defaults = local dev stack. For the Docker stack: API_URL=http://127.0.0.1:8080/api/v1 (through nginx)
# and N8N_EXEC=docker (n8n CLI inside the wa-agent-n8n container).
N8N = os.environ.get("N8N_URL", "http://127.0.0.1:5690/webhook")
API = os.environ.get("API_URL", "http://127.0.0.1:8000/api/v1")
N8N_EXEC = os.environ.get("N8N_EXEC", "local")
SECRET = os.environ["WA_APP_SECRET"]           # the running backend uses the same value
PREFIX = "9230099"                             # all e2e customers use this phone prefix (cleaned first)
results: list[tuple[str, bool, str]] = []


def env(key: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].split("#")[0].strip()
    return ""


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def meta_payload(wa_id: str, name: str, text: str, wamid: str) -> bytes:
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "E2E", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "E2E"},
            "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
            "messages": [{"from": wa_id, "id": wamid, "timestamp": str(int(time.time())), "type": "text",
                          "text": {"body": text}}]}}]}]}, ensure_ascii=False).encode()


async def customer_says(http, wa_id, name, text, wamid, signature=None):
    raw = meta_payload(wa_id, name, text, wamid)
    sig = signature or "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    r = await http.post(f"{N8N}/whatsapp", content=raw,
                        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    await asyncio.sleep(4)                     # n8n continues (AI, reply, sub-workflows) after answering
    return r


async def db(sql: str, *args):
    import asyncpg
    conn = await asyncpg.connect(user=env("POSTGRES_USER"), password=env("POSTGRES_PASSWORD"),
                                 host="127.0.0.1", port=int(env("POSTGRES_PORT")), database=env("POSTGRES_DB"))
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def conversation(http, h, wa_id):
    items = (await http.get(f"{API}/conversations", params={"q": wa_id}, headers=h)).json()["items"]
    return (await http.get(f"{API}/conversations/{items[0]['id']}", headers=h)).json() if items else None


def n8n_execute(workflow_id: str) -> bool:
    if N8N_EXEC == "docker":
        # Separate task-broker port: the running n8n in the same container already uses the default.
        cmd = ["docker", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5692", "wa-agent-n8n",
               "n8n", "execute", f"--id={workflow_id}"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        return "Execution was successful" in out.stdout
    e = dict(os.environ, N8N_USER_FOLDER=str(ROOT), N8N_ENCRYPTION_KEY=env("N8N_ENCRYPTION_KEY"),
             N8N_RUNNERS_BROKER_PORT="5692", N8N_DIAGNOSTICS_ENABLED="false")
    out = subprocess.run(["cmd", "/c", f"n8n execute --id={workflow_id}"], env=e, capture_output=True,
                         text=True, timeout=240)
    return "Execution was successful" in out.stdout


async def main():
    started = datetime.now(UTC)
    # Clean previous e2e data (only our test prefix)
    await db(f"DELETE FROM customers WHERE wa_id LIKE '{PREFIX}%'")
    await db("DELETE FROM automation_errors WHERE workflow_id = 'WaAgentErrDrill1'")

    async with httpx.AsyncClient(timeout=30) as http:
        tok = (await http.post(f"{API}/auth/login", json={"email": env("ADMIN_EMAIL"),
                                                          "password": env("ADMIN_PASSWORD")})).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}

        # 1) Meta webhook verification through n8n
        r = await http.get(f"{N8N}/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": env("WA_VERIFY_TOKEN"),
                                                      "hub.challenge": "E2E_CHALLENGE"})
        check("1. Meta verification via n8n echoes challenge", r.status_code == 200 and r.text == "E2E_CHALLENGE")
        r = await http.get(f"{N8N}/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                                      "hub.challenge": "x"})
        check("   wrong verify token rejected", r.status_code == 403, f"HTTP {r.status_code}")

        # 2) FAQ question -> AI reply delivered (mock)
        a = f"{PREFIX}00001"
        r = await customer_says(http, a, "E2E Ayesha", "Do you provide support after launch?", "wamid.E2E.1")
        conv = await conversation(http, h, a)
        msgs = conv["messages"] if conv else []
        ai = [m for m in msgs if m["sender_type"] == "ai"]
        check("2. Customer question saved + AI reply sent", r.status_code == 200 and len(msgs) == 2 and
              bool(ai) and ai[0]["send_status"] == "mocked", f"reply: {(ai[0]['body'][:60] if ai else '-')}")

        # 3) Meta delivers the same webhook again
        await customer_says(http, a, "E2E Ayesha", "Do you provide support after launch?", "wamid.E2E.1")
        check("3. Duplicate webhook: no second message / reply",
              len((await conversation(http, h, a))["messages"]) == 2)

        # 4) Forged signature
        r = await customer_says(http, a, "E2E Ayesha", "fake", "wamid.E2E.FAKE", signature="sha256=" + "0" * 64)
        check("4. Forged signature rejected (401), nothing stored", r.status_code == 401 and
              len((await conversation(http, h, a))["messages"]) == 2, f"HTTP {r.status_code}")

        # 5) Human handoff
        b = f"{PREFIX}00002"
        await customer_says(http, b, "E2E Bilal", "Please let me talk to a human", "wamid.E2E.2")
        conv = await conversation(http, h, b)
        notif = await db("SELECT changes->>'subject' AS s FROM audit_logs WHERE action='notification.sent' "
                         "AND created_at > $1 AND changes->>'subject' LIKE '%E2E Bilal%'", started)
        check("5. Handoff: AI paused, agent notified (WA 3)", conv["mode"] == "human" and conv["has_pending_handoff"]
              and len(notif) >= 1, f"notifications: {len(notif)}")
        await customer_says(http, b, "E2E Bilal", "Hello?", "wamid.E2E.3")
        conv = await conversation(http, h, b)
        check("   next customer message gets NO AI reply", conv["messages"][-1]["sender_type"] == "customer")

        # 6) Human replies from the dashboard, then resumes the AI
        r = await http.post(f"{API}/conversations/{conv['id']}/reply", headers=h,
                            json={"text": "Hi Bilal, Sara here. How can I help?"})
        check("6. Human reply sent from dashboard", r.status_code == 201 and r.json()["send_status"] == "mocked")
        r = await http.post(f"{API}/conversations/{conv['id']}/resume", headers=h)
        check("   Resume AI", r.status_code == 200 and r.json()["mode"] == "ai")

        # 7) STOP -> opt-out
        await customer_says(http, a, "E2E Ayesha", "STOP", "wamid.E2E.4")
        cust = await db("SELECT opted_out_at FROM customers WHERE wa_id=$1", a)
        last = (await conversation(http, h, a))["messages"][-1]
        check("7. STOP: opted out + confirmation sent", cust[0]["opted_out_at"] is not None and
              "unsubscribed" in (last["body"] or ""))

        # 8) Follow-up: eligible lead -> plan (n8n) -> approve (dashboard) -> send (n8n) -> once
        c = f"{PREFIX}00003"
        # A question the FAQ fallback can answer: "Hi" would (correctly) hand off to a human
        # when Gemini is not configured, and handed-off chats are not followed up automatically.
        await customer_says(http, c, "E2E Zara", "What services do you offer?", "wamid.E2E.5")
        then = datetime.now(UTC) - timedelta(hours=30)
        await db("UPDATE customers SET marketing_opt_in=true WHERE wa_id=$1", c)
        await db("UPDATE messages SET created_at=$1 WHERE conversation_id IN (SELECT v.id FROM conversations v "
                 "JOIN customers cu ON cu.id=v.customer_id WHERE cu.wa_id=$2)", then, c)
        await db("UPDATE conversations SET last_customer_message_at=$1, last_message_at=$1 WHERE customer_id="
                 "(SELECT id FROM customers WHERE wa_id=$2)", then, c)
        await db("INSERT INTO leads (customer_id, temperature, status, score) SELECT id, 'warm', 'new', 50 "
                 "FROM customers WHERE wa_id=$1", c)
        planned = n8n_execute("WaAgentFollowUp1")
        tasks = (await http.get(f"{API}/follow-ups", params={"status": "pending_approval"}, headers=h)).json()["items"]
        mine = [t for t in tasks if t["customer_wa_id"] == c]
        check("8. Follow-up planned by WA 4, waiting for approval", planned and len(mine) == 1)
        if mine:
            await http.post(f"{API}/follow-ups/{mine[0]['id']}/approve", headers=h)
            n8n_execute("WaAgentFollowUp1")
            n8n_execute("WaAgentFollowUp1")         # second run must NOT send again
            sent = await db("SELECT count(*) AS n FROM messages m JOIN conversations v ON v.id=m.conversation_id "
                            "JOIN customers cu ON cu.id=v.customer_id WHERE cu.wa_id=$1 AND m.sender_type='system'", c)
            check("   approved follow-up sent exactly once (template)", sent[0]["n"] == 1, f"sent {sent[0]['n']}")

        # 9) Error drill -> WA 5 -> recorded, grouped, alerted once
        for _ in range(2):
            await http.post(f"{N8N}/error-drill")
            await asyncio.sleep(6)
        errs = await db("SELECT status, occurrences, alerted_at FROM automation_errors WHERE workflow_id='WaAgentErrDrill1'")
        check("9. Failed workflow -> WA 5: 1 grouped error, x2, alerted, awaiting approval",
              len(errs) == 1 and errs[0]["occurrences"] == 2 and errs[0]["alerted_at"] is not None
              and errs[0]["status"] == "needs_approval",
              f"rows={len(errs)} " + (f"x{errs[0]['occurrences']} {errs[0]['status']}" if errs else ""))

    write_report(started)
    return all(ok for _, ok, _ in results)


def write_report(started):
    passed = sum(ok for _, ok, _ in results)
    stack = ("Docker Compose: postgres + backend + nginx/frontend + n8n 2.46.4" if N8N_EXEC == "docker"
             else "local: PostgreSQL 16 (Docker), FastAPI, n8n 2.11.3")
    lines = [
        "# End-to-end test results", "",
        f"Run: {started:%Y-%m-%d %H:%M} UTC · stack: {stack} (6 workflows) · "
        "WhatsApp **mock mode** · Gemini **not configured** (FAQ fallback path)", "",
        f"**{passed}/{len(results)} checks passed**", "",
        "| Check | Result | Detail |", "|---|---|---|",
        *[f"| {n.strip()} | {'✅ pass' if ok else '❌ FAIL'} | {d} |" for n, ok, d in results], "",
        "Reproduce: `.\\scripts\\run-e2e.ps1`", "",
        "_Not covered here (needs real accounts): real Meta delivery, real Gemini answers, Gmail sending._",
    ]
    (ROOT / "docs" / "TEST_RESULTS_E2E.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ok = asyncio.run(main())
    print(f"\n{'ALL PASSED' if ok else 'SOME CHECKS FAILED'} - report: docs/TEST_RESULTS_E2E.md")
    sys.exit(0 if ok else 1)
