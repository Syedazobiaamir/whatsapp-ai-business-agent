# Applies all database migrations and loads the sample data.
# Usage (from the project root):  .\scripts\db-migrate.ps1
# Safe to run any time: migrations already applied are skipped, seed data is upserted.

$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "..\backend"
$python  = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Python venv not found. Create it first (see README)." -ForegroundColor Red
    exit 1
}

Push-Location $backend
try {
    Write-Host "1/2 Applying migrations..." -ForegroundColor Cyan
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Migration failed" }

    Write-Host "2/2 Loading seed data..." -ForegroundColor Cyan
    & $python -m app.seed
    if ($LASTEXITCODE -ne 0) { throw "Seed failed" }

    Write-Host "Database ready." -ForegroundColor Green
}
finally {
    Pop-Location
}
