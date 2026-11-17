# Starts the FastAPI backend in development mode (auto-reloads when you save a file).
# Usage (from the project root):  .\scripts\start-backend.ps1
# Then open http://127.0.0.1:8000/docs   (stop with Ctrl+C)

$backend = Join-Path $PSScriptRoot "..\backend"
$uvicorn = Join-Path $backend ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path $uvicorn)) {
    Write-Host "Backend venv not found. See README 'Quick start'." -ForegroundColor Red
    exit 1
}

$health = docker inspect -f '{{.State.Health.Status}}' wa-agent-postgres 2>$null
if ($health -ne "healthy") {
    Write-Host "Database is not running. Start it with: docker compose up -d" -ForegroundColor Yellow
    exit 1
}

& $uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir $backend
