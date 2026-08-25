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

function Import-DotEnv([string]$path) {
    $vars = @{}
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return }
        $idx = $line.IndexOf('=')
        if ($idx -lt 1) { return }
        $vars[$line.Substring(0, $idx)] = $line.Substring($idx + 1)
    }
    return $vars
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

# 2>&1 は PowerShell 5.1 で NativeCommandError を生成し誤って catch に入るため使わない。
# 起動直後は daemon が応答するまで少し時間がかかるので最大3回リトライする。
$dockerReady = $false
for ($i = 1; $i -le 3; $i++) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    if ($i -lt 3) {
        Write-Warn "Docker daemon not responding yet (attempt $i/3). Retrying in 5 seconds..."
        Start-Sleep -Seconds 5
    }
}
if (-not $dockerReady) {
    Write-Fail "Docker is not running. Please start Docker Desktop and try again."
}
Write-OK "Docker is running."

docker compose version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "docker compose not found. Please update Docker Desktop to 4.x or later."
}
Write-OK "docker compose plugin found."

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
    $platformAdminPass = New-RandomHex 16

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
    $env_content = $env_content -replace '(PLATFORM_ADMIN_PASSWORD=)changeme_platform_admin_password', "`${1}$platformAdminPass"

    # SMTP: keep as-is (users configure their own SMTP)
    $env_content | Set-Content ".env" -Encoding UTF8
    Write-OK ".env generated with random secrets."

    Write-Host @"

    +-------------------------------------------------+
    |  SAVE THESE CREDENTIALS                         |
    |  (also stored in .env — keep it out of git)     |
    +-------------------------------------------------+
    PostgreSQL password  : $pgPass
    InfluxDB password    : $influxPass
    InfluxDB token       : $influxToken
    EMQX dashboard       : $emqxPass
    MinIO password       : $minioPass
    Grafana password     : $grafanaPass
    JWT secret           : $jwtSecret
    Platform admin login : see PLATFORM_ADMIN_EMAIL / PLATFORM_ADMIN_PASSWORD below
    +-------------------------------------------------+
"@ -ForegroundColor Yellow
}

$envVars = Import-DotEnv ".env"
$platformDomain = $envVars['PLATFORM_DOMAIN']
if ([string]::IsNullOrWhiteSpace($platformDomain)) { $platformDomain = 'localhost' }
$adminEmail = $envVars['PLATFORM_ADMIN_EMAIL']
if ([string]::IsNullOrWhiteSpace($adminEmail)) { $adminEmail = 'admin@platform.local' }
$adminPassword = $envVars['PLATFORM_ADMIN_PASSWORD']

# ---- pull images ------------------------------------------------------------

Write-Step "Pulling Docker images (this may take a few minutes)..."
docker compose pull
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose pull failed." }
Write-OK "Images ready."

# ---- bootstrap TLS certificates (Step-CA) ------------------------------------
# nginx bind-mounts certs/server/server.crt and server.key. If those files don't
# exist yet when `docker compose up -d` runs, Docker silently creates them as
# empty directories instead, and nginx fails to start with a PEM parse error.
# So the CA must come up and issue the server cert *before* the rest of the
# stack starts.

Write-Step "Bootstrapping TLS certificates (Step-CA)..."

New-Item -ItemType Directory -Force -Path "certs/ca", "certs/server", "step-ca/data" | Out-Null

docker compose up -d step-ca
if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to start step-ca." }

Write-Host "    Waiting for Step-CA" -NoNewline
$maxRetries = 20
$retries = 0
while ($true) {
    docker compose exec -T step-ca step ca health --ca-url=https://localhost:9000 --root=/home/step/certs/root_ca.crt *> $null
    if ($LASTEXITCODE -eq 0) { break }
    $retries++
    if ($retries -ge $maxRetries) {
        Write-Host ""
        Write-Fail "Step-CA did not become healthy (check: docker compose logs step-ca)."
    }
    Start-Sleep -Seconds 3
    Write-Host "." -NoNewline
}
Write-Host " healthy" -ForegroundColor Green

docker compose cp step-ca:/home/step/certs/root_ca.crt certs/ca/root_ca.crt

docker compose exec -T step-ca step ca certificate $platformDomain /tmp/server.crt /tmp/server.key `
    --ca-url=https://localhost:9000 `
    --root=/home/step/certs/root_ca.crt `
    --provisioner=iot-platform `
    --provisioner-password-file=/home/step/secrets/password `
    --not-after=24h `
    --san=$platformDomain `
    --san=localhost `
    --force
if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to issue server certificate." }

docker compose cp step-ca:/tmp/server.crt certs/server/server.crt
docker compose cp step-ca:/tmp/server.key certs/server/server.key

Write-OK "Server certificate issued for $platformDomain."

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

# ---- bootstrap platform admin account ----------------------------------------
# platform_users starts empty — nothing else creates the first login. Seed one
# via core-api's own DB session/hasher so the hash format always matches what
# /auth/login verifies against. Skips if an account with this email exists
# already, so re-running the installer never resets a real admin's password.

Write-Step "Bootstrapping platform admin account..."

$pyScript = @'
import os
from app.database import SessionLocal
from app.models.public import PlatformUser
from app.services.auth import hash_password

email = os.environ["PLATFORM_ADMIN_EMAIL"]
password = os.environ["PLATFORM_ADMIN_PASSWORD"]
with SessionLocal() as db:
    if db.query(PlatformUser).filter(PlatformUser.email == email).first():
        print(f"[skip] platform admin {email} already exists")
    else:
        db.add(PlatformUser(email=email, password_hash=hash_password(password)))
        db.commit()
        print(f"[ok] created platform admin {email}")
'@

$pyScript | docker compose exec -T -e "PLATFORM_ADMIN_EMAIL=$adminEmail" -e "PLATFORM_ADMIN_PASSWORD=$adminPassword" core-api python3 -
if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to bootstrap platform admin account." }

Write-OK "Platform admin ready ($adminEmail)."

# ---- done -------------------------------------------------------------------

Write-Host @"

  +---------------------------------------------------------+
  |  IoT Platform is running!                               |
  +---------------------------------------------------------+
  Admin / Login  https://localhost/admin/
                 $adminEmail / see PLATFORM_ADMIN_PASSWORD in .env
  Grafana        https://localhost/grafana/  (behind admin login)
  MailHog        http://localhost:8025
  InfluxDB       http://localhost:8086

  Core API, EMQX dashboard and MinIO console are internal-only
  (not published to the host) - reach them via
  'docker compose exec <service> ...' or the /api/ proxy above.
  +---------------------------------------------------------+
  Credentials are stored in .env (keep this file private)
  +---------------------------------------------------------+

  The TLS certificate is issued by this project's own local CA
  (certs\ca\root_ca.crt), so your browser will flag it as
  untrusted. Trust it once (run PowerShell as Administrator):
    certutil -addstore -f "ROOT" certs\ca\root_ca.crt
  (Chrome/Edge may need a restart to pick up the new trust store)

  Next steps:
    1. Open https://localhost/admin/ and log in as
       $adminEmail (password in .env)
    2. Create a tenant
    3. Provision your first device using the bootstrap token

  To stop:   docker compose down
  To view logs: docker compose logs -f

"@ -ForegroundColor Cyan
