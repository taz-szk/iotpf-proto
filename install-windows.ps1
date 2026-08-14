#Requires -Version 5.1
<#
.SYNOPSIS
    IoT Platform — Windows installer (Docker Desktop required)
.DESCRIPTION
    Generates secrets, writes .env, and starts all services via docker compose.
    Run from the iot-platform project root directory.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---- helpers ----------------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    [!!] $msg" -ForegroundColor Yellow
}

function Write-Fail([string]$msg) {
    Write-Host "`n[FAIL] $msg" -ForegroundColor Red
    exit 1
}

function New-RandomHex([int]$byteCount) {
    $buf = [byte[]]::new($byteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buf)
    ($buf | ForEach-Object { $_.ToString('x2') }) -join ''
}

function New-RandomBase64([int]$byteCount) {
    $buf = [byte[]]::new($byteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buf)
    [System.Convert]::ToBase64String($buf) -replace '[+/=]', '_'
}

# ---- banner -----------------------------------------------------------------

Write-Host @"

  ___ ___ _____   ___ _      _    _    __
 |_ _/ _ \_   _| | _ \ |__ _| |_ / _|/ _|__ _ _ _ _ __
  | | (_) || |   |  _/ / _' |  _|  _| (_/ _' | '_| '  \
 |___\___/ |_|   |_| |_\__,_|\__|_|  \__\__,_|_| |_|_|_|

  Windows Installer  (Docker Desktop required)
  ---------------------------------------------
"@ -ForegroundColor Cyan

# ---- check working directory ------------------------------------------------

if (-not (Test-Path "docker-compose.yml")) {
    Write-Fail "docker-compose.yml not found. Run this script from the iot-platform root directory."
}

# ---- check Docker Desktop ---------------------------------------------------

Write-Step "Checking Docker Desktop..."

try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-OK "Docker is running."
} catch {
    Write-Fail "Docker is not running. Please start Docker Desktop and try again."
}

try {
    $null = docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-OK "docker compose plugin found."
} catch {
    Write-Fail "docker compose not found. Please update Docker Desktop to 4.x or later."
}

# ---- generate .env ----------------------------------------------------------

Write-Step "Setting up environment file..."

if (Test-Path ".env") {
    Write-Warn ".env already exists. Skipping generation (using existing file)."
    Write-Warn "Delete .env and re-run to regenerate secrets."
} else {
    if (-not (Test-Path ".env.example")) {
        Write-Fail ".env.example not found."
    }

    $pgPass       = New-RandomHex 24
    $influxPass   = New-RandomHex 24
    $influxToken  = New-RandomHex 32
    $emqxPass     = New-RandomHex 16
    $emqxCookie   = New-RandomBase64 20
    $minioPass    = New-RandomHex 20
    $stepCaPass   = New-RandomHex 20
    $grafanaPass  = New-RandomHex 16
    $jwtSecret    = New-RandomHex 32
    $webhookSecret = New-RandomHex 32

    $env_content = Get-Content ".env.example" -Raw
    $env_content = $env_content -replace 'changeme_strong_password(?=\r?\nINFLUXDB_ADMIN_PASSWORD)', $pgPass
    $env_content = $env_content -replace '(POSTGRES_PASSWORD=)changeme_strong_password', "`${1}$pgPass"
    $env_content = $env_content -replace '(INFLUXDB_ADMIN_PASSWORD=)changeme_strong_password', "`${1}$influxPass"
    $env_content = $env_content -replace '(INFLUXDB_ADMIN_TOKEN=)changeme_admin_token_min_64_characters_required_use_openssl_rand_hex_32', "`${1}$influxToken"
    $env_content = $env_content -replace '(EMQX_DASHBOARD_PASSWORD=)changeme_strong_password', "`${1}$emqxPass"
    $env_content = $env_content -replace '(EMQX_NODE_COOKIE=)changeme_cluster_cookie_min_20_chars', "`${1}$emqxCookie"
    $env_content = $env_content -replace '(MINIO_ROOT_PASSWORD=)changeme_strong_password', "`${1}$minioPass"
    $env_content = $env_content -replace '(STEP_CA_PASSWORD=)changeme_ca_password', "`${1}$stepCaPass"
    $env_content = $env_content -replace '(GRAFANA_ADMIN_PASSWORD=)changeme_strong_password', "`${1}$grafanaPass"
    $env_content = $env_content -replace '(JWT_SECRET=)changeme_jwt_secret_min_32_characters_replace_in_prod', "`${1}$jwtSecret"
    $env_content = $env_content -replace '(EMQX_WEBHOOK_SECRET=)changeme_webhook_secret_min_32_characters_replace_in_prod', "`${1}$webhookSecret"

    # SMTP: keep as-is (users configure their own SMTP)
    $env_content | Set-Content ".env" -Encoding UTF8
    Write-OK ".env generated with random secrets."

    Write-Host @"

    +-------------------------------------------------+
    |  SAVE THESE CREDENTIALS                         |
    |  (also stored in .env — keep it out of git)     |
    +-------------------------------------------------+
    PostgreSQL password : $pgPass
    InfluxDB password   : $influxPass
    InfluxDB token      : $influxToken
    EMQX dashboard      : $emqxPass
    MinIO password      : $minioPass
    Grafana password    : $grafanaPass
    JWT secret          : $jwtSecret
    +-------------------------------------------------+
"@ -ForegroundColor Yellow
}

# ---- pull images ------------------------------------------------------------

Write-Step "Pulling Docker images (this may take a few minutes)..."
docker compose pull
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose pull failed." }
Write-OK "Images ready."

# ---- start services ---------------------------------------------------------

Write-Step "Starting services..."
docker compose up -d
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose up failed." }
Write-OK "Containers started."

# ---- wait for health --------------------------------------------------------

Write-Step "Waiting for services to become healthy (up to 120 seconds)..."

$services = @('postgres', 'influxdb', 'step-ca', 'emqx', 'core-api')
$deadline = (Get-Date).AddSeconds(120)

foreach ($svc in $services) {
    Write-Host "    Waiting for $svc..." -NoNewline
    while ((Get-Date) -lt $deadline) {
        $status = docker inspect --format='{{.State.Health.Status}}' $svc 2>$null
        if ($status -eq 'healthy') {
            Write-Host " healthy" -ForegroundColor Green
            break
        }
        $state = docker inspect --format='{{.State.Status}}' $svc 2>$null
        if ($state -eq 'exited') {
            Write-Host " exited (check: docker logs $svc)" -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 3
        Write-Host "." -NoNewline
    }
}

Write-Host ""

# ---- done -------------------------------------------------------------------

Write-Host @"

  +---------------------------------------------------------+
  |  IoT Platform is running!                               |
  +---------------------------------------------------------+
  Core API      http://localhost:8000/docs
  Grafana       http://localhost:3000
  EMQX          http://localhost:18083
  MinIO         http://localhost:9001
  MailHog       http://localhost:8025
  InfluxDB      http://localhost:8086
  +---------------------------------------------------------+
  Credentials are stored in .env (keep this file private)
  +---------------------------------------------------------+

  Next steps:
    1. Open http://localhost:8000/docs and create a tenant
    2. Log in at http://localhost:3000 (admin / see .env)
    3. Provision your first device using the bootstrap token

  To stop:   docker compose down
  To view logs: docker compose logs -f

"@ -ForegroundColor Cyan
