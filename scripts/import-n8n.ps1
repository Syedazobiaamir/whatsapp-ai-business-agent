# Imports the backend credential + the 4 workflows into the PROJECT's n8n and publishes them.
# Run while n8n is STOPPED (then start it with .\scripts\start-n8n.ps1).
#   .\scripts\import-n8n.ps1
#   .\scripts\import-n8n.ps1 -BackendUrl http://backend:8000/api/v1   (e.g. inside Docker)
param([string]$BackendUrl = "http://127.0.0.1:8000/api/v1")
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "n8n-env.ps1")

$key = Get-DotEnv "BACKEND_INTERNAL_API_KEY"
if ($key.Length -lt 20) { throw "BACKEND_INTERNAL_API_KEY in .env is missing or too short" }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("n8n-import-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $tmp | Out-Null
try {
    # 1) Credential: n8n encrypts it with N8N_ENCRYPTION_KEY on import. The temp file is deleted below.
    $cred = @{ id = "WaAgentBackendK1"; name = "WA Agent Backend (internal key)"; type = "httpHeaderAuth";
               data = @{ name = "X-Internal-Api-Key"; value = $key } }
    $credFile = Join-Path $tmp "credentials.json"
    # "[ ... ]": n8n expects a JSON array (PowerShell 5.1 has no ConvertTo-Json -AsArray)
    [IO.File]::WriteAllText($credFile, "[" + (ConvertTo-Json $cred -Depth 5) + "]")
    n8n import:credentials --input="$credFile"
    if ($LASTEXITCODE -ne 0) { throw "credential import failed" }

    # 2) Workflows (backend URL replaced if different)
    $wfDir = Join-Path $tmp "workflows"; New-Item -ItemType Directory $wfDir | Out-Null
    Get-ChildItem (Join-Path $ProjectRoot "n8n\workflows\*.json") | ForEach-Object {
        $json = [IO.File]::ReadAllText($_.FullName).Replace("http://127.0.0.1:8000/api/v1", $BackendUrl.TrimEnd("/"))
        [IO.File]::WriteAllText((Join-Path $wfDir $_.Name), $json)
    }
    n8n import:workflow --separate --input="$wfDir"
    if ($LASTEXITCODE -ne 0) { throw "workflow import failed" }

    # 3) Publish (= activate). Sub-workflows first.
    # The publish CLI occasionally hangs (e.g. database briefly locked right after n8n stopped),
    # so each attempt gets a time limit and one retry.
    foreach ($id in "WaAgentErrors001", "WaAgentLeadNoti1", "WaAgentHandoff01", "WaAgentFollowUp1",
                    "WaAgentIncoming1", "WaAgentErrDrill1") {
        $ok = $false
        foreach ($attempt in 1, 2) {
            $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "n8n publish:workflow --id=$id" `
                 -PassThru -NoNewWindow
            $null = $p.Handle   # PowerShell quirk: without this, ExitCode stays empty after exit
            if ($p.WaitForExit(120000) -and $p.ExitCode -eq 0) { $ok = $true; break }
            if (-not $p.HasExited) { taskkill /T /F /PID $p.Id | Out-Null }
            Write-Host "Publish of $id timed out/failed (attempt $attempt), retrying..." -ForegroundColor Yellow
            Start-Sleep 5
        }
        if (-not $ok) { throw "publish failed for $id" }
        Write-Host "Published $id" -ForegroundColor Green
    }
    Write-Host "Imported and published. Start n8n: .\scripts\start-n8n.ps1" -ForegroundColor Green
}
finally {
    Remove-Item $tmp -Recurse -Force        # never leave the plain-text key on disk
}
