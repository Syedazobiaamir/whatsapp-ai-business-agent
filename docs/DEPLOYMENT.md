# Deployment

Two ways to run the complete system with Docker:

| | Local full stack | Production server |
|---|---|---|
| Command | `docker compose --profile full up -d --build` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile full up -d --build` |
| Dashboard | http://127.0.0.1:8080 | https://DASHBOARD_DOMAIN |
| n8n / Meta webhook | http://127.0.0.1:5690/webhook/whatsapp | https://N8N_DOMAIN/webhook/whatsapp |
| HTTPS | — | Caddy, automatic Let's Encrypt |
| PostgreSQL | 16 (keeps your dev data) | 17 (full test suite passes on 17.12) |
| WhatsApp | mock mode allowed | `APP_ENV=production`, mock mode **refused** |

> 💰 **Cost approval needed:** a server (VPS) and a domain cost money. Nothing here is bought or activated automatically. Choose and pay for them yourself, or get approval first.

## Architecture in Docker
```
Internet ──443──► Caddy ─┬─► frontend (nginx: React app, /api proxy) ──► backend (FastAPI) ──► postgres
                         └─► n8n (webhooks, editor) ─────────────────────► backend
Only Caddy publishes ports (80/443). Everything else talks on Docker's private network.
```
| Service | What it does |
|---|---|
| `postgres` | App database plus a separate `n8n` database |
| `db-init` | One-off: creates the `n8n` database if it is missing |
| `backend` | Runs `alembic upgrade head`, inserts missing seed data, then serves the API (2 workers, non-root user) |
| `frontend` | nginx: static React build, `/api` proxy, security headers (CSP, X-Frame-Options, nosniff) |
| `n8n-import` | One-off: imports the credential and 6 workflows **only when the JSON files changed**, so edits made in the editor survive restarts. `N8N_FORCE_IMPORT=true` forces it |
| `n8n` | Workflow engine (pinned `n8nio/n8n:2.46.4`) |
| `caddy` | Production only: HTTPS and reverse proxy; blocks the demo error-drill URL |

## Local full stack (Windows)
```powershell
docker compose --profile full up -d --build   # first build: about 3-5 minutes
docker compose --profile full ps              # all "healthy"; db-init / n8n-import "Exited (0)"
start http://127.0.0.1:8080                   # log in with ADMIN_EMAIL / ADMIN_PASSWORD from .env
docker compose --profile full down            # stop (data stays in the volumes)
```
The local scripts (`start-backend.ps1`, `start-n8n.ps1`) use the same ports. Stop the Docker stack before using them.

**Low-memory PCs:** cap Docker in `%USERPROFILE%\.wslconfig` (`[wsl2]` `memory=3GB` `processors=2`), then run `wsl --shutdown`. Build one image at a time: `docker compose --profile full build backend`, then `frontend`.

## Production on a Linux VPS (Ubuntu 24.04, 2 vCPU / 4 GB RAM recommended)
1. **Server:** install Docker (`curl -fsSL https://get.docker.com | sh`). Open only ports 22, 80 and 443 (`ufw allow OpenSSH; ufw allow 80; ufw allow 443; ufw enable`).
2. **DNS:** create A-records for `DASHBOARD_DOMAIN` and `N8N_DOMAIN` pointing to the server IP.
3. **Code:** `git clone <your repo> /opt/wa-agent && cd /opt/wa-agent`
4. **Secrets:** `cp .env.example .env`, then fill in **new** random values. Never reuse your dev `.env`.
   - `POSTGRES_PASSWORD`, `JWT_SECRET` (32+ characters), `ADMIN_PASSWORD`, `BACKEND_INTERNAL_API_KEY`, `N8N_ENCRYPTION_KEY`, `WA_VERIFY_TOKEN` (`openssl rand -hex 24` makes good ones)
   - WhatsApp production values (see [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) §5): `WA_PHONE_NUMBER_ID`, permanent System User `WA_ACCESS_TOKEN`, `WA_APP_SECRET`, `WA_ENV=production`
   - `GEMINI_API_KEY`. Customer data needs the **paid tier** (the free tier may use prompts for training); approve that cost first.
   - `DASHBOARD_DOMAIN`, `N8N_DOMAIN`, `ACME_EMAIL`, `SALES_NOTIFY_EMAIL`, `ADMIN_ALERT_EMAIL`
   - `chmod 600 .env`
5. **Start:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile full up -d --build`
6. **Meta:** set the webhook callback to `https://N8N_DOMAIN/webhook/whatsapp` with your verify token, and subscribe to `messages`.
7. **n8n:** open `https://N8N_DOMAIN`, create the owner account, connect Gmail if wanted ([N8N_SETUP.md](N8N_SETUP.md) §3), and create an API key for `N8N_API_KEY` (Automation page and automatic retries). Restart the backend after editing `.env`: `docker compose ... up -d backend`.
8. **Backups:** `crontab -e` → `0 3 * * * cd /opt/wa-agent && sh deploy/backup.sh >> /var/log/wa-agent-backup.log 2>&1`. This keeps 14 days of the app and n8n databases. Copy them off the server regularly, and **test a restore**.

## Updating
```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile full up -d --build
```
Migrations run automatically on backend start. Changed workflow files are re-imported automatically.

## Production checklist
- [ ] New random secrets; `.env` permissions 600; `.env` never committed
- [ ] `APP_ENV=production` (set by the prod file): `/docs` hidden, mock mode refused, WhatsApp credentials required
- [ ] HTTPS works on both domains (Caddy logs: `docker logs wa-agent-caddy`)
- [ ] Meta webhook verified; a real test message is answered
- [ ] `WA_APP_SECRET` set, so every webhook is signature-checked
- [ ] n8n owner account created with a strong password
- [ ] Daily backups running, and one restore tested
- [ ] `ADMIN_ALERT_EMAIL` receives the WA 5 alerts (send a test via the drill locally; it is blocked in production)

## Facts verified locally (2026-11-17)
- The full stack builds and starts on Windows (Docker Desktop, WSL2 capped at 3 GB / 2 CPUs). Images: backend 356 MB, frontend 93 MB.
- End-to-end test against the Docker stack: **13/13** (`API_URL=http://127.0.0.1:8080/api/v1 N8N_EXEC=docker python tests/e2e/run_e2e.py`).
- Dashboard through nginx in a real browser: 8 views, 0 console errors with the CSP enabled.
- Backend tests on PostgreSQL 17.12: **197 passed**.
- The production compose file and Caddyfile validate (`docker compose … config`, `caddy validate`).

**Not tested:** a real VPS, real domains/HTTPS certificates, real Meta/Gemini traffic. These need accounts and costs that need your approval.
