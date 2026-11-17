# Starts the project's n8n on http://127.0.0.1:5690 (stop with Ctrl+C).
# Optional: -WebhookUrl https://<tunnel>.trycloudflare.com  (public URL Meta will call)
param([string]$WebhookUrl = "")
. (Join-Path $PSScriptRoot "n8n-env.ps1")
if ($WebhookUrl) { $env:WEBHOOK_URL = $WebhookUrl.TrimEnd("/") + "/" }
Write-Host "n8n editor: http://127.0.0.1:$($env:N8N_PORT)   data: $ProjectRoot\.n8n" -ForegroundColor Cyan
n8n start
