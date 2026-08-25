#!/usr/bin/env bash
# IoT Platform — macOS installer (Docker Desktop required)
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

  macOS Installer  (Docker Desktop required)
  ------------------------------------------${NC}
"

# ---- check working directory ------------------------------------------------

[[ -f "docker-compose.yml" ]] || fail "docker-compose.yml not found. Run this script from the iot-platform root directory."

# ---- check Docker -----------------------------------------------------------

step "Checking Docker Desktop..."

if ! docker info &>/dev/null; then
    fail "Docker is not running. Please start Docker Desktop and try again."
fi
ok "Docker is running."

if ! docker compose version &>/dev/null; then
    fail "docker compose not found. Please update Docker Desktop to 4.x or later."
fi
ok "docker compose plugin found."

if ! command -v openssl &>/dev/null; then
    fail "openssl not found. Install it via: brew install openssl"
fi
ok "openssl found."

# ---- generate .env ----------------------------------------------------------

step "Setting up environment file..."

if [[ -f ".env" ]]; then
    warn ".env already exists. Skipping generation (using existing file)."
    warn "Delete .env and re-run to regenerate secrets."
else
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
        .env.example > .env

    ok ".env generated with random secrets."

    echo -e "${YELLOW}
    +-------------------------------------------------+
    |  SAVE THESE CREDENTIALS                         |
    |  (also stored in .env — keep it out of git)     |
    +-------------------------------------------------+
    PostgreSQL password : ${PG_PASS}
    InfluxDB password   : ${INFLUX_PASS}
    InfluxDB token      : ${INFLUX_TOKEN}
    EMQX dashboard      : ${EMQX_PASS}
    MinIO password      : ${MINIO_PASS}
    Grafana password    : ${GRAFANA_PASS}
    JWT secret          : ${JWT_SECRET}
    +-------------------------------------------------+${NC}
"
fi

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

# shellcheck disable=SC1091
source .env

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

for svc in postgres influxdb step-ca emqx core-api; do
    wait_healthy "$svc" || true
done

# ---- done -------------------------------------------------------------------

echo -e "${CYAN}
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

  To stop:      docker compose down
  To view logs: docker compose logs -f
${NC}"
