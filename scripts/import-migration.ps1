# import-migration.ps1
# IoT Platform migration importer (run on new PC)
#
# Usage:
#   1. Install Docker Desktop on the new PC
#   2. Unzip iot-platform-code.zip somewhere
#   3. From the unzipped folder, run:
#      .\scripts\import-migration.ps1 -MigrationDir "C:\path\to\iot-platform-migration"

param(
    [Parameter(Mandatory=$true)]
    [string]$MigrationDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir

Write-Host ""
Write-Host "===== IoT Platform Migration Import =====" -ForegroundColor Cyan
Write-Host "Migration folder: $MigrationDir"
Write-Host "Project folder:   $projectDir"
Write-Host ""

if (-not (Test-Path $MigrationDir)) {
    Write-Error "Migration folder not found: $MigrationDir"
    exit 1
}

$dockerCheck = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker Desktop is not running. Please start it and retry."
    exit 1
}

# --- 1. .env ---
Write-Host "[1/5] Copying .env..." -ForegroundColor Yellow
$envSrc = Join-Path $MigrationDir ".env"
$envDst = Join-Path $projectDir ".env"
if (Test-Path $envSrc) {
    if (Test-Path $envDst) {
        Copy-Item $envDst "$envDst.bak" -Force
        Write-Host "  Existing .env backed up as .env.bak"
    }
    Copy-Item $envSrc $envDst -Force
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Warning "  .env not found in migration folder"
}

# --- 2. TLS certs ---
Write-Host "[2/5] Copying TLS certificates..." -ForegroundColor Yellow
$certsSrc = Join-Path $MigrationDir "certs"
$certsDst = Join-Path $projectDir "certs"
if (Test-Path $certsSrc) {
    if (Test-Path $certsDst) { Remove-Item -Recurse -Force $certsDst }
    Copy-Item -Recurse $certsSrc $certsDst
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Warning "  certs/ not found in migration folder"
}

# --- 3. Docker volumes ---
Write-Host "[3/5] Importing Docker volumes..." -ForegroundColor Yellow

$volumes = @("postgres_data","influxdb_data","grafana_data","step_ca_data","emqx_data","minio_data")

foreach ($vol in $volumes) {
    $fullName = "iot-platform_$vol"
    $tarFile  = Join-Path $MigrationDir "volumes\$vol.tar.gz"

    if (-not (Test-Path $tarFile)) {
        Write-Warning "  $vol.tar.gz not found (skip)"
        continue
    }

    Write-Host "  - $fullName ..."
    docker volume create $fullName | Out-Null
    docker run --rm `
        -v "${fullName}:/data" `
        -v "$MigrationDir\volumes:/backup" `
        alpine sh -c "cd /data && rm -rf ./* ./..?* ./.[!.]* 2>/dev/null; tar xzf /backup/$vol.tar.gz -C /data"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    OK" -ForegroundColor Green
    } else {
        Write-Warning "    FAILED"
    }
}

# --- 4. Simulator device certs ---
Write-Host "[4/5] Copying simulator device certs..." -ForegroundColor Yellow
$simSrc = Join-Path $MigrationDir "simulator-certs"
$simDst = Join-Path $projectDir "simulator\certs"
if (Test-Path $simSrc) {
    if (Test-Path $simDst) { Remove-Item -Recurse -Force $simDst }
    Copy-Item -Recurse $simSrc $simDst
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Host "  No simulator certs (skip)"
}

# --- 5. Start containers ---
Write-Host "[5/5] Starting containers..." -ForegroundColor Yellow
Set-Location $projectDir
docker compose up -d
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Warning "  docker compose up failed. Check: docker compose logs"
}

# --- Done ---
Write-Host ""
Write-Host "===== Import Complete =====" -ForegroundColor Cyan
Write-Host ""
Write-Host "IMPORTANT - Check these items:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  [A] If the IP/hostname changed from the original PC:"
Write-Host "      - Edit .env: set PLATFORM_DOMAIN to the new IP or hostname"
Write-Host "      - Regenerate the server TLS certificate (see below)"
Write-Host "      - Re-configure simulator to connect to the new IP"
Write-Host ""
Write-Host "  [B] Regenerate server cert (only if IP/domain changed):"
Write-Host "      docker compose exec core-api python -c """
Write-Host "        from app.services.cert import issue_server_cert; issue_server_cert()"
Write-Host "      """
Write-Host "      Then restart nginx:"
Write-Host "      docker compose restart nginx"
Write-Host ""
Write-Host "  [C] Open firewall ports on the new PC:"
Write-Host "      443  (HTTPS - web UI & API)"
Write-Host "      8883 (MQTT over TLS - devices)"
Write-Host "      8025 (MailHog - optional)"
Write-Host ""
Write-Host "  [D] Simulator exe:"
Write-Host "      The simulator certs are restored under simulator\certs\"
Write-Host "      If simulator is on a separate machine, copy the exe + certs folder there."
Write-Host ""
