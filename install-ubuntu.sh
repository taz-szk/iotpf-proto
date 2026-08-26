#!/usr/bin/env bash
# IoT Platform — Ubuntu installer (Docker Desktop required)
# Run from the iot-platform project root directory.
set -euo pipefail

# ---- helpers ----------------------------------------------------------------

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

step()  { echo -e "\n${CYAN}==> $*${NC}"; }
ok()    { echo -e "    ${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "    ${YELLOW}[!!]${NC} $*"; }
fail()  { echo -e "\n${RED}[FAIL]${NC} $*"; exit 1; }

rand_hex() { openssl rand -hex "$1"; }

# ---- banner -----------------------------------------------------------------

echo -e "${CYAN}
  ___ ___ _____   ___ _      _    _    __
 |_ _/ _ \\_   _| | _ \\ |__ _| |_ / _|/ _|__ _ _ _ _ __
  | | (_) || |   |  _/ / _' |  _|  _| (_/ _' | '_| '  \\
 |___\\___/ |_|   |_| |_\\__,_|\\__|_|  \\__\\__,_|_| |_|_|_|

  Ubuntu Installer  (Docker Desktop required)
  -------------------------------------------${NC}
"

# ---- check working directory ------------------------------------------------

[[ -f "docker-compose.yml" ]] || fail "docker-compose.yml not found. Run this script from the iot-platform root directory."

# ---- check Docker -----------------------------------------------------------

step "Checking Docker Desktop..."

if ! docker info &>/dev/null; then
    echo ""
    echo "  Docker is not running. On Ubuntu you can start Docker Desktop with:"
    echo "    systemctl --user start docker-desktop"
    echo "  Or launch it from the Applications menu."
    fail "Docker is not running."
fi
ok "Docker is running."

if ! docker compose version &>/dev/null; then
    fail "docker compose not found. Please update Docker Desktop to 4.x or later."
fi
ok "docker compose plugin found."

# ---- check openssl ----------------------------------------------------------

if ! command -v openssl &>/dev/null; then
    step "openssl not found — installing..."
    sudo apt-get update -qq
    sudo apt-get install -y openssl
fi
ok "openssl found."

# ---- generate .env ----------------------------------------------------------

step "Setting up environment file..."

ENV_GENERATED=false
if [[ -f ".env" ]]; then
    warn ".env already exists. Skipping generation (using existing file)."
    warn "Delete .env and re-run to regenerate secrets."
else
    ENV_GENERATED=true
    [[ -f ".env.example" ]] || fail ".env.example not found."

    PG_PASS=$(rand_hex 24)
    INFLUX_PASS=$(rand_hex 24)
    INFLUX_TOKEN=$(rand_hex 32)
    EMQX_PASS=$(rand_hex 16)
    EMQX_COOKIE=$(rand_hex 20)
    MINIO_PASS=$(rand_hex 20)
    STEP_PASS=$(rand_hex 20)
    GRAFANA_PASS=$(rand_hex 16)
    JWT_SECRET=$(rand_hex 32)
    WEBHOOK_SECRET=$(rand_hex 32)
    PLATFORM_ADMIN_PASS=$(rand_hex 16)

    sed \
        -e "s|changeme_strong_password|${PG_PASS}|" \
        -e "s|INFLUXDB_ADMIN_PASSWORD=.*|INFLUXDB_ADMIN_PASSWORD=${INFLUX_PASS}|" \
        -e "s|INFLUXDB_ADMIN_TOKEN=.*|INFLUXDB_ADMIN_TOKEN=${INFLUX_TOKEN}|" \
        -e "s|EMQX_DASHBOARD_PASSWORD=.*|EMQX_DASHBOARD_PASSWORD=${EMQX_PASS}|" \
        -e "s|EMQX_NODE_COOKIE=.*|EMQX_NODE_COOKIE=${EMQX_COOKIE}|" \
        -e "s|MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=${MINIO_PASS}|" \
        -e "s|STEP_CA_PASSWORD=.*|STEP_CA_PASSWORD=${STEP_PASS}|" \
        -e "s|GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=${GRAFANA_PASS}|" \
        -e "s|JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" \
        -e "s|EMQX_WEBHOOK_SECRET=.*|EMQX_WEBHOOK_SECRET=${WEBHOOK_SECRET}|" \
        -e "s|PLATFORM_ADMIN_PASSWORD=.*|PLATFORM_ADMIN_PASSWORD=${PLATFORM_ADMIN_PASS}|" \
        .env.example > .env

    # Secure .env (readable by owner only)
    chmod 600 .env
    ok ".env generated with random secrets (chmod 600)."

    echo -e "${YELLOW}
    +-------------------------------------------------+
    |  SAVE THESE CREDENTIALS                         |
    |  (also stored in .env — keep it out of git)     |
    +-------------------------------------------------+
    PostgreSQL password  : ${PG_PASS}
    InfluxDB password    : ${INFLUX_PASS}
    InfluxDB token       : ${INFLUX_TOKEN}
    EMQX dashboard       : ${EMQX_PASS}
    MinIO password       : ${MINIO_PASS}
    Grafana password     : ${GRAFANA_PASS}
    JWT secret           : ${JWT_SECRET}
    Platform admin login : see PLATFORM_ADMIN_EMAIL / PLATFORM_ADMIN_PASSWORD below
    +-------------------------------------------------+${NC}
"
fi

# 新しい .env を生成したのに既存の postgres データボリュームが残っていると
# パスワード不一致で認証エラーになる。早期に検出して案内する。
if [[ "$ENV_GENERATED" == "true" ]]; then
    if docker volume ls --format "{{.Name}}" 2>/dev/null | grep -q "^iot-platform_postgres_data$"; then
        fail "Postgres データボリューム (iot-platform_postgres_data) が既に存在しています。
新しい .env を生成したため、ボリューム内のパスワードと一致しません。

古いデータを削除してから再実行してください:
  docker compose down -v
  rm .env
  ./install-ubuntu.sh"
    fi
fi

# shellcheck disable=SC1091
source .env

# ---- pull images ------------------------------------------------------------

step "Pulling Docker images (this may take a few minutes)..."
docker compose pull
ok "Images ready."

# ---- bootstrap TLS certificates (Step-CA) ------------------------------------
# nginx bind-mounts certs/server/server.crt and server.key. If those files don't
# exist yet when `docker compose up -d` runs, Docker silently creates them as
# empty directories instead, and nginx fails to start with a PEM parse error.
# So the CA must come up and issue the server cert *before* the rest of the
# stack starts.

step "Bootstrapping TLS certificates (Step-CA)..."

mkdir -p certs/ca certs/server step-ca/data

docker compose up -d step-ca

printf "    Waiting for Step-CA"
MAX_RETRIES=20
RETRIES=0
until docker compose exec -T step-ca \
  step ca health --ca-url=https://localhost:9000 \
  --root=/home/step/certs/root_ca.crt &>/dev/null; do
    RETRIES=$((RETRIES + 1))
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        echo
        fail "Step-CA did not become healthy (check: docker compose logs step-ca)."
    fi
    sleep 3
    printf "."
done
echo -e " ${GREEN}healthy${NC}"

# デバイス証明書の最大有効期間を 24h → 8760h（1年）に拡張
docker compose exec -T step-ca sh -c \
  "sed -i 's/\"maxTLSCertDuration\": \"24h0m0s\"/\"maxTLSCertDuration\": \"8760h0m0s\"/g' /home/step/config/ca.json"
docker compose restart step-ca
printf "    Applying certificate policy"
RETRIES=0
until docker compose exec -T step-ca \
  step ca health --ca-url=https://localhost:9000 \
  --root=/home/step/certs/root_ca.crt &>/dev/null; do
    RETRIES=$((RETRIES + 1))
    if [ "$RETRIES" -ge 10 ]; then echo; fail "Step-CA failed to restart after policy update."; fi
    sleep 3; printf "."
done
echo -e " ${GREEN}done${NC}"

docker compose cp step-ca:/home/step/certs/root_ca.crt certs/ca/root_ca.crt

docker compose exec -T step-ca \
  step ca certificate \
    "${PLATFORM_DOMAIN:-localhost}" \
    /tmp/server.crt \
    /tmp/server.key \
    --ca-url=https://localhost:9000 \
    --root=/home/step/certs/root_ca.crt \
    --provisioner=iot-platform \
    --provisioner-password-file=/home/step/secrets/password \
    --not-after=24h \
    --san="${PLATFORM_DOMAIN:-localhost}" \
    --san=localhost \
    --force

docker compose cp step-ca:/tmp/server.crt certs/server/server.crt
docker compose cp step-ca:/tmp/server.key certs/server/server.key

ok "Server certificate issued for ${PLATFORM_DOMAIN:-localhost}."

# ---- start services ---------------------------------------------------------

step "Starting services..."
docker compose up -d
ok "Containers started."

# ---- wait for health --------------------------------------------------------

step "Waiting for services to become healthy (up to 120 seconds)..."

wait_healthy() {
    local svc=$1
    local deadline=$(( $(date +%s) + 120 ))
    printf "    Waiting for %s" "$svc"
    while true; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "")
        if [[ "$status" == "healthy" ]]; then
            echo -e " ${GREEN}healthy${NC}"
            return 0
        fi
        local state
        state=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "")
        if [[ "$state" == "exited" ]]; then
            echo -e " ${RED}exited (check: docker logs $svc)${NC}"
            return 1
        fi
        if (( $(date +%s) >= deadline )); then
            echo -e " ${YELLOW}timeout${NC}"
            return 1
        fi
        sleep 3
        printf "."
    done
}

for svc in postgres influxdb step-ca emqx; do
    wait_healthy "$svc" || true
done

# core-api は bootstrap の直前に確実に healthy になっているか確認する
if ! wait_healthy "core-api"; then
    fail "core-api did not become healthy. Check: docker compose logs core-api"
fi

# ---- bootstrap platform admin account ----------------------------------------
# platform_users starts empty — nothing else creates the first login. Seed one
# via core-api's own DB session/hasher so the hash format always matches what
# /auth/login verifies against. Skips if an account with this email exists
# already, so re-running the installer never resets a real admin's password.

step "Bootstrapping platform admin account..."

docker compose exec -T \
  -e PLATFORM_ADMIN_EMAIL="${PLATFORM_ADMIN_EMAIL:-admin@platform.local}" \
  -e PLATFORM_ADMIN_PASSWORD="${PLATFORM_ADMIN_PASSWORD}" \
  core-api python3 - <<'PYEOF'
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
PYEOF

ok "Platform admin ready (${PLATFORM_ADMIN_EMAIL:-admin@platform.local})."

# ---- done -------------------------------------------------------------------

echo -e "${CYAN}
  +---------------------------------------------------------+
  |  IoT Platform is running!                               |
  +---------------------------------------------------------+
  Admin / Login  https://localhost/admin/
                 ${PLATFORM_ADMIN_EMAIL:-admin@platform.local} / see PLATFORM_ADMIN_PASSWORD in .env
  Grafana        https://localhost/grafana/  (behind admin login)
  MailHog        http://localhost:8025
  InfluxDB       http://localhost:8086

  Core API, EMQX dashboard and MinIO console are internal-only
  (not published to the host) — reach them via
  'docker compose exec <service> ...' or the /api/ proxy above.
  +---------------------------------------------------------+
  Credentials are stored in .env (chmod 600, keep it private)
  +---------------------------------------------------------+

  The TLS certificate is issued by this project's own local CA
  (certs/ca/root_ca.crt), so your browser will flag it as
  untrusted. Trust it once system-wide, or:
    sudo cp certs/ca/root_ca.crt /usr/local/share/ca-certificates/iot-platform-ca.crt
    sudo update-ca-certificates
  (Chrome/Firefox may need the cert imported into their own trust
  store too — see chrome://settings/certificates or about:preferences#privacy)

  Next steps:
    1. Open https://localhost/admin/ and log in as
       ${PLATFORM_ADMIN_EMAIL:-admin@platform.local} (password in .env)
    2. Create a tenant
    3. Provision your first device using the bootstrap token

  To stop:      docker compose down
  To view logs: docker compose logs -f
${NC}"
