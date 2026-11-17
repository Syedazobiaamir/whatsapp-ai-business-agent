# Runs the end-to-end test against the REAL stack (DB + backend + n8n), mock WhatsApp.
# Starts backend and n8n itself (n8n must not already run on 5690), stops them afterwards.
# Usage:  .\scripts\run-e2e.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
if ((docker inspect -f '{{.State.Health.Status}}' wa-agent-postgres 2>$null) -ne "healthy") { throw "Start the database first: docker compose up -d" }
if (Get-NetTCPConnection -LocalPort 8000,5690 -State Listen -ErrorAction SilentlyContinue) { throw "Stop the backend/n8n first (ports 8000/5690 in use)" }

$secret = "e2e-" + [guid]::NewGuid().ToString("N")
$env:WA_APP_SECRET = $secret            # backend verifies signatures; the test signs with the same value
$env:WA_MOCK_MODE = "true"
$logs = Join-Path ([IO.Path]::GetTempPath()) "wa-agent-e2e"; New-Item -ItemType Directory -Force $logs | Out-Null
$backend = Start-Process -FilePath "backend\.venv\Scripts\uvicorn.exe" -ArgumentList "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--app-dir", "backend" `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput "$logs\backend.out" -RedirectStandardError "$logs\backend.err"
$n8n = Start-Process -FilePath "powershell" -ArgumentList "-ExecutionPolicy", "Bypass", "-File", "scripts\start-n8n.ps1" `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput "$logs\n8n.out" -RedirectStandardError "$logs\n8n.err"
try {
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep 2; if ((Get-Content "$logs\n8n.out" -Raw -ErrorAction SilentlyContinue) -match "Editor is now accessible") { break } }
    Write-Host "Stack is up. Running end-to-end checks..." -ForegroundColor Cyan
    & backend\.venv\Scripts\python.exe tests\e2e\run_e2e.py
    $code = $LASTEXITCODE
}
finally {
    taskkill /T /F /PID $n8n.Id 2>&1 | Out-Null
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    $env:WA_APP_SECRET = $null
}
exit $code
