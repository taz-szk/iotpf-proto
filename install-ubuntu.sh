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

    # Secure .env (readable by owner only)
    chmod 600 .env
    ok ".env generated with random secrets (chmod 600)."

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
  Credentials are stored in .env (chmod 600, keep it private)
  +---------------------------------------------------------+

  Next steps:
    1. Open http://localhost:8000/docs and create a tenant
    2. Log in at http://localhost:3000 (admin / see .env)
    3. Provision your first device using the bootstrap token

  To stop:      docker compose down
  To view logs: docker compose logs -f
${NC}"
