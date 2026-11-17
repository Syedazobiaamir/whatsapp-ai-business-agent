# Chat with the AI agent from the terminal (mock WhatsApp: nothing is really sent).
# The backend must be running (.\scripts\start-backend.ps1) with WA_MOCK_MODE=true.
# Usage:
#   .\scripts\try-ai.ps1 -Text "How much is an online store?"
#   .\scripts\try-ai.ps1 -Text "Budget is 1500 USD, start in 2 weeks" -WaId 923001112222
param(
    [Parameter(Mandatory = $true)][string]$Text,
    [string]$WaId = "923001112222",
    [string]$Name = "Demo Customer",
    [string]$Api = "http://127.0.0.1:8000/api/v1"
)
$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot "..\.env"
function Get-EnvValue($key) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$key\s*=\s*([^#\s]+)" } | Select-Object -First 1
    if ($line) { $null = $line -match "^\s*$key\s*=\s*([^#\s]+)"; return $Matches[1] }
}

$login = @{ email = (Get-EnvValue "ADMIN_EMAIL"); password = (Get-EnvValue "ADMIN_PASSWORD") } | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post "$Api/auth/login" -ContentType "application/json" -Body $login).access_token
$body = @{ text = $Text; wa_id = $WaId; name = $Name } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post "$Api/dev/simulate-inbound" -Headers @{ Authorization = "Bearer $token" } `
     -ContentType "application/json" -Body $body

foreach ($d in $r.decisions) {
    Write-Host "Customer : $Text" -ForegroundColor Cyan
    Write-Host "AI reply : $($d.reply_text)" -ForegroundColor Green
    Write-Host ("action={0} intent={1} confidence={2} modules={3} fallback={4}" -f `
        $d.action, $d.intent, $d.confidence, ($d.modules -join ","), $d.fallback_used) -ForegroundColor DarkGray
    if ($d.lead) { Write-Host ("lead: {0} ({1}/100) - {2}" -f $d.lead.temperature.ToUpper(), $d.lead.score, $d.lead.reason) -ForegroundColor Yellow }
    if ($d.guardrails.Count) { Write-Host "guardrails: $($d.guardrails -join ', ')" -ForegroundColor Red }
}
