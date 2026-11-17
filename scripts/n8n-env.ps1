# Shared settings for the PROJECT's n8n instance. Dot-source it:  . .\scripts\n8n-env.ps1
# Data lives in <project>\.n8n (git-ignored), so your personal n8n (~\.n8n, port 5678) is untouched.

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $ProjectRoot ".env"

function Get-DotEnv([string]$Key, [string]$Default = "") {
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$Key\s*=\s*([^#\s]+)" } | Select-Object -First 1
    if ($line) { $null = $line -match "^\s*$Key\s*=\s*([^#\s]+)"; return $Matches[1] }
    return $Default
}

$env:N8N_USER_FOLDER = $ProjectRoot                       # -> <project>\.n8n
$env:N8N_ENCRYPTION_KEY = Get-DotEnv "N8N_ENCRYPTION_KEY"
$env:N8N_PORT = Get-DotEnv "N8N_PORT" "5690"
$env:N8N_LISTEN_ADDRESS = "127.0.0.1"                     # not reachable from your network
$env:N8N_RUNNERS_BROKER_PORT = "5691"                     # default 5679 is used by your personal n8n
$env:N8N_DIAGNOSTICS_ENABLED = "false"
$env:N8N_VERSION_NOTIFICATIONS_ENABLED = "false"
$env:GENERIC_TIMEZONE = "Asia/Karachi"
$env:N8N_SECURE_COOKIE = "false"                          # local http only; use https in production
if (-not $env:N8N_ENCRYPTION_KEY) { throw "N8N_ENCRYPTION_KEY missing in .env" }
