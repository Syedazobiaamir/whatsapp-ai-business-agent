# Checks that the project's PostgreSQL container is running and answering queries.
# Usage (from the project root):  .\scripts\db-status.ps1

$container = "wa-agent-postgres"
$envFile   = Join-Path $PSScriptRoot "..\.env"

# Read POSTGRES_USER and POSTGRES_DB from .env (ignores comments after the value)
$cfg = @{}
Get-Content $envFile | Where-Object { $_ -match '^\s*(POSTGRES_USER|POSTGRES_DB)\s*=\s*([^#\s]+)' } |
    ForEach-Object { $null = $_ -match '^\s*(\w+)\s*=\s*([^#\s]+)'; $cfg[$Matches[1]] = $Matches[2] }

$health = docker inspect -f '{{.State.Health.Status}}' $container 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Container '$container' not found. Run: docker compose up -d" -ForegroundColor Red
    exit 1
}
Write-Host "Container health: $health"

docker exec $container psql -U $cfg.POSTGRES_USER -d $cfg.POSTGRES_DB -c "SELECT now() AS db_time, current_database() AS db;"
if ($LASTEXITCODE -eq 0) { Write-Host "Database OK" -ForegroundColor Green } else { Write-Host "Database query failed" -ForegroundColor Red; exit 1 }
