# Starts the React dashboard on http://127.0.0.1:5173 (backend must run on :8000).
# First time: installs packages (node_modules, ~130 MB).
$frontend = Join-Path $PSScriptRoot "..\frontend"
Push-Location $frontend
try {
    if (-not (Test-Path "node_modules")) { npm install --no-fund --no-audit }
    npm run dev
}
finally { Pop-Location }
