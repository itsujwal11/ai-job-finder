# One-time setup + start. Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function New-Secret([int]$Length = 40) {
    $chars = [char[]]"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

if (-not (Test-Path ".env")) {
    $content = Get-Content ".env.example" -Raw
    $content = $content -replace "(?m)^ENGINE_API_TOKEN=.*$", "ENGINE_API_TOKEN=$(New-Secret 40)"
    $content = $content -replace "(?m)^POSTGRES_PASSWORD=.*$", "POSTGRES_PASSWORD=$(New-Secret 32)"
    $content = $content -replace "(?m)^N8N_ENCRYPTION_KEY=.*$", "N8N_ENCRYPTION_KEY=$(New-Secret 40)"
    [System.IO.File]::WriteAllText("$PWD\.env", $content, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Created .env with random secrets. Add your ANTHROPIC_API_KEY to it, then re-run this script." -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force "output" | Out-Null

Write-Host "Building and starting containers (first time takes a few minutes)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed - is Docker Desktop running?" }

Write-Host "Waiting for the engine..."
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try { if ((Invoke-WebRequest -UseBasicParsing http://localhost:8080/api/health -TimeoutSec 3).StatusCode -eq 200) { $ok = $true; break } } catch {}
    Start-Sleep -Seconds 3
}
if (-not $ok) { throw "Engine did not become healthy. Check: docker compose logs engine" }

Write-Host "Importing n8n workflows..."
docker compose run --rm n8n-import
if ($LASTEXITCODE -ne 0) { throw "Workflow import failed. Check: docker compose logs n8n" }
docker compose restart n8n | Out-Null

docker compose exec engine python -m app.cli check
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:8080"
Write-Host "  n8n:       http://localhost:5679  (create your owner account the first time)"
