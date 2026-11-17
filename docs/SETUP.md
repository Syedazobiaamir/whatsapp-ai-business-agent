# Setup guide (Windows, from zero)

Two ways to run the project. **A** is the fastest; **B** is for developing.

## Prerequisites
| Tool | Version used | Needed for |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 29 | Everything (database, or the whole stack) |
| [Git](https://git-scm.com/) | 2.51 | Getting the code |
| [Python](https://www.python.org/) | **3.12** | Option B (backend, tests) |
| [Node.js](https://nodejs.org/) | 24 | Option B (dashboard) |
| n8n (`npm install -g n8n`) | 2.11+ | Option B (workflows) |

Low on RAM (8 GB)? Create `%USERPROFILE%\.wslconfig` with `[wsl2]`, `memory=3GB`, `processors=2`, then run `wsl --shutdown`.

## 1. Get the code and create `.env`
```powershell
git clone <repository-url> whatsapp-ai-business-agent
cd whatsapp-ai-business-agent
Copy-Item .env.example .env
```
Open `.env` and replace **every** placeholder secret with a long random value. This PowerShell line prints one:
```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | % {[char]$_})
```
Fill at least `POSTGRES_PASSWORD`, `JWT_SECRET` (32+ characters), `ADMIN_EMAIL`, `ADMIN_PASSWORD` (10+ characters), `WA_VERIFY_TOKEN`, `BACKEND_INTERNAL_API_KEY` and `N8N_ENCRYPTION_KEY`.
Keep `WA_MOCK_MODE=true` until you connect Meta. **Comments go on their own line**, never after a value.

## Option A: everything in Docker
```powershell
docker compose --profile full up -d --build      # first time: 3-5 minutes
docker compose --profile full ps                 # wait until all are "healthy"
start http://127.0.0.1:8080                      # dashboard: ADMIN_EMAIL / ADMIN_PASSWORD
```
n8n runs at http://127.0.0.1:5690 (create the owner account on first visit). The workflows are already imported and active.

## Option B: development mode
```powershell
docker compose up -d                                             # PostgreSQL only
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements-dev.txt
.\scripts\db-migrate.ps1                                         # tables + sample business data
.\scripts\start-backend.ps1                                      # terminal 1: API :8000 (/docs)
.\scripts\start-frontend.ps1                                     # terminal 2: dashboard :5173
.\scripts\import-n8n.ps1                                         # once, n8n stopped
.\scripts\start-n8n.ps1                                          # terminal 3: n8n :5690
```

## 2. Try it without a phone
```powershell
backend\.venv\Scripts\python scripts\demo_data.py               # realistic demo leads & chats
.\scripts\try-ai.ps1 -Text "Do you work with international clients?"
.\scripts\send-test-webhook.ps1 -Url http://127.0.0.1:5690/webhook/whatsapp -Text "What are your hours?"
```
With no `GEMINI_API_KEY`, answers come from the FAQ search (fallback). Add a free key from https://aistudio.google.com/apikey for real AI answers. **Don't enable billing without approval.** On the free tier, use test data only.

## 3. Optional connections
| What | Guide |
|---|---|
| Real WhatsApp (Meta test number, free) | [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) |
| Gmail notifications, n8n API key | [N8N_SETUP.md](N8N_SETUP.md) |
| Production server with HTTPS | [DEPLOYMENT.md](DEPLOYMENT.md) |

## 4. Run the tests
```powershell
backend\.venv\Scripts\python -m pytest --cov     # 197 tests
cd frontend; npm test; cd ..                     # 18 tests
.\scripts\run-e2e.ps1                            # end-to-end (stop the Docker stack first)
```

## Troubleshooting
| Problem | Fix |
|---|---|
| Port already in use (5432/5678/8000) | Another app uses it. This project uses 5433 (DB) and 5690 (n8n) on purpose; stop the other app or change `.env` |
| Every database call takes ~2 s | Use `POSTGRES_HOST=127.0.0.1`, not `localhost` (an IPv6 delay on Windows) |
| Docker build: `failed to stat parent … no such file` | The build cache was damaged by a crash: `docker builder prune -af`, then build again |
| In Docker, messages are "ignored" | An inline comment in `.env` was read as a value. Put comments on their own line |
| PC freezes or restarts during builds | Cap WSL memory (see Prerequisites); build one image at a time: `docker compose --profile full build backend` |
| `ADMIN_PASSWORD … too weak` | Use 10+ characters in `.env`, then rerun `.\scripts\db-migrate.ps1` |
