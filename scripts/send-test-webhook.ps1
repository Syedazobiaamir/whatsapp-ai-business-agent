# Sends a fake "customer message" to the backend webhook, signed exactly like Meta signs it.
# Usage (backend must be running):
#   .\scripts\send-test-webhook.ps1 -Text "How much is a website?"
#   .\scripts\send-test-webhook.ps1 -Text "Hi" -MessageId "wamid.SAME"   # run twice to test duplicates
param(
    [string]$Text = "Hi, how much does an online store cost?",
    [string]$WaId = "923001234567",
    [string]$Name = "Ali Khan",
    [string]$MessageId = "wamid.TEST.$([guid]::NewGuid().ToString('N'))",
    [string]$Url = "http://127.0.0.1:8000/api/v1/webhooks/whatsapp"
)
$ErrorActionPreference = "Stop"
$root = Join-Path $PSScriptRoot ".."

# Build the payload from the Meta-format fixture
$payload = Get-Content (Join-Path $root "tests\fixtures\meta_text_message.json") -Raw | ConvertFrom-Json
$value = $payload.entry[0].changes[0].value
$value.contacts[0].wa_id = $WaId
$value.contacts[0].profile.name = $Name
$msg = $value.messages[0]
$msg.from = $WaId
$msg.id = $MessageId
$msg.timestamp = [string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$msg.text.body = $Text
$bytes = [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 20 -Compress))

# The secret: environment variable first, then .env
$secret = $env:WA_APP_SECRET
if (-not $secret) {
    $line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -match '^\s*WA_APP_SECRET\s*=\s*([^#\s]+)' } | Select-Object -First 1
    if ($line) { $null = $line -match '^\s*WA_APP_SECRET\s*=\s*([^#\s]+)'; $secret = $Matches[1] }
}

$headers = @{}
if ($secret) {
    # HMAC-SHA256 over the exact bytes we send = what Meta does
    $hmac = New-Object System.Security.Cryptography.HMACSHA256 (,[Text.Encoding]::UTF8.GetBytes($secret))
    $sig = -join ($hmac.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
    $headers["X-Hub-Signature-256"] = "sha256=$sig"
    Write-Host "Signed with WA_APP_SECRET" -ForegroundColor DarkGray
} else {
    Write-Host "WA_APP_SECRET not set: sending UNSIGNED (only accepted in development)" -ForegroundColor Yellow
}

$resp = Invoke-WebRequest -Uri $Url -Method Post -Body $bytes -ContentType "application/json" -Headers $headers -UseBasicParsing
Write-Host "HTTP $($resp.StatusCode)  message id: $MessageId" -ForegroundColor Green
$resp.Content
