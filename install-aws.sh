#!/usr/bin/env bash
# IoT Platform — AWS EC2 installer (Docker Engine + Let's Encrypt)
# Usage: CERTBOT_EMAIL=you@example.com ./install-aws.sh
# Or:    ./install-aws.sh you@example.com
set -euo pipefail

# ---- helpers ----------------------------------------------------------------

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

step()  { echo -e "\n${CYAN}==> $*${NC}"; }
ok()    { echo -e "    ${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "    ${YELLOW}[!!]${NC} $*"; }
fail()  { echo -e "\n${RED}[FAIL]${NC} $*"; exit 1; }

rand_hex() { openssl rand -hex "$1"; }

PLATFORM_DOMAIN="${PLATFORM_DOMAIN:-iot.suzuki-net.org}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-${1:-}}"

# ---- banner -----------------------------------------------------------------

echo -e "${CYAN}
  ___ ___ _____ _   ___ ____      __  __
 |_ _/ _ \_   _|/_\ |_ _|  _ \ ___\ \/ /
  | | (_) || | / _ \ | ||   /|___/ >  <
 |___\___/ |_|/_/ \_\___||_|_\    /_/\_\

  AWS EC2 Installer  (Docker Engine + Let's Encrypt)
  Domain: ${PLATFORM_DOMAIN}
  ---------------------------------------------------${NC}
"

# ---- validate inputs --------------------------------------------------------

[[ -f "docker-compose.yml" ]] || fail "docker-compose.yml not found. Run from the iot-platform root directory."

if [[ -z "$CERTBOT_EMAIL" ]]; then
    fail "CERTBOT_EMAIL not set.\nUsage: CERTBOT_EMAIL=you@example.com ./install-aws.sh"
fi

# ---- install Docker Engine --------------------------------------------------

step "Checking Docker Engine..."
if ! command -v docker &>/dev/null; then
    step "Installing Docker Engine..."
    sudo apt-get update -qq
    sudo apt-get install -y ca-certificates curl gnupg lsb-release
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker "$USER"
    ok "Docker Engine installed."
    warn "Docker group added — applying with 'newgrp docker' for this session."
    # グループ変更をこのプロセスに即時反映
    exec newgrp docker <<INNEREOF
PLATFORM_DOMAIN="${PLATFORM_DOMAIN}" CERTBOT_EMAIL="${CERTBOT_EMAIL}" bash "$(realpath "$0")"
INNEREOF
fi

docker_ready=false
for i in 1 2 3; do
    if docker info &>/dev/null; then docker_ready=true; break; fi
    if [ "$i" -lt 3 ]; then warn "Docker not ready (attempt $i/3). Retrying in 5s..."; sleep 5; fi
done
[ "$docker_ready" = "true" ] || fail "Docker is not running. Try: sudo systemctl start docker"
ok "Docker is running."

docker compose version &>/dev/null || fail "docker compose plugin not found."
ok "docker compose plugin found."

# ---- install tools ----------------------------------------------------------

step "Installing openssl and certbot..."
if ! command -v openssl &>/dev/null; then sudo apt-get install -y openssl; fi
if ! command -v certbot &>/dev/null; then sudo apt-get install -y certbot; fi
ok "openssl and certbot ready."

# ---- swap (2 GB) ------------------------------------------------------------

step "Configuring swap (2 GB)..."
if [[ ! -f /swapfile ]]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    ok "2 GB swap created and enabled."
else
    ok "Swap already configured ($(free -h | awk '/^Swap:/{print $2}'))."
fi

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
        -e "s|PLATFORM_DOMAIN=.*|PLATFORM_DOMAIN=${PLATFORM_DOMAIN}|" \
        .env.example > .env

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
    Platform admin login : see PLATFORM_ADMIN_EMAIL / PLATFORM_ADMIN_PASSWORD in .env
    +-------------------------------------------------+${NC}
"
fi

# 新しい .env を生成したのに既存の postgres データボリュームが残っているとパスワード不一致になる
if [[ "$ENV_GENERATED" == "true" ]]; then
    if docker volume ls --format "{{.Name}}" 2>/dev/null | grep -q "^iot-platform_postgres_data$"; then
        fail "Postgres データボリューム (iot-platform_postgres_data) が既に存在しています。
古いデータを削除してから再実行してください:
  docker compose down -v
  rm .env
  CERTBOT_EMAIL=${CERTBOT_EMAIL} ./install-aws.sh"
    fi
fi

# shellcheck disable=SC1091
source .env

# ---- pull images ------------------------------------------------------------

step "Pulling Docker images (this may take a few minutes)..."
docker compose pull
ok "Images ready."

# ---- bootstrap step-ca (device mTLS CA) ------------------------------------

step "Bootstrapping Step-CA (device mTLS certificate authority)..."

mkdir -p certs/ca certs/server step-ca/data

docker compose up -d step-ca

printf "    Waiting for Step-CA"
MAX_RETRIES=20; RETRIES=0
until docker compose exec -T step-ca \
  step ca health --ca-url=https://localhost:9000 \
  --root=/home/step/certs/root_ca.crt &>/dev/null; do
    RETRIES=$((RETRIES + 1))
    [ "$RETRIES" -ge "$MAX_RETRIES" ] && echo && fail "Step-CA did not become healthy (check: docker compose logs step-ca)."
    sleep 3; printf "."
done
echo -e " ${GREEN}healthy${NC}"

# デバイス証明書の最大有効期間を 1年 に拡張
docker compose cp step-ca:/home/step/config/ca.json /tmp/iotpf_ca.json
python3 - <<'PYEOF'
import json
with open('/tmp/iotpf_ca.json') as f:
    ca = json.load(f)
for p in ca['authority']['provisioners']:
    if p['name'] == 'iot-platform':
        p.setdefault('claims', {})['maxTLSCertDuration'] = '8760h0m0s'
        p.setdefault('claims', {})['defaultTLSCertDuration'] = '24h0m0s'
with open('/tmp/iotpf_ca.json', 'w') as f:
    json.dump(ca, f, indent=4)
PYEOF
docker compose cp /tmp/iotpf_ca.json step-ca:/home/step/config/ca.json
rm /tmp/iotpf_ca.json
docker compose restart step-ca

printf "    Applying certificate policy"
RETRIES=0
until docker compose exec -T step-ca \
  step ca health --ca-url=https://localhost:9000 \
  --root=/home/step/certs/root_ca.crt &>/dev/null; do
    RETRIES=$((RETRIES + 1))
    [ "$RETRIES" -ge 10 ] && echo && fail "Step-CA failed to restart after policy update."
    sleep 3; printf "."
done
echo -e " ${GREEN}done${NC}"

docker compose cp step-ca:/home/step/certs/root_ca.crt certs/ca/root_ca.crt

# EMQX 用サーバー証明書（step-ca 発行 — デバイス mTLS に使用）
docker compose exec -T step-ca \
  step ca certificate \
    "${PLATFORM_DOMAIN}" \
    /tmp/server.crt \
    /tmp/server.key \
    --ca-url=https://localhost:9000 \
    --root=/home/step/certs/root_ca.crt \
    --provisioner=iot-platform \
    --provisioner-password-file=/home/step/secrets/password \
    --not-after=8760h \
    --san="${PLATFORM_DOMAIN}" \
    --san=localhost \
    --force

docker compose cp step-ca:/tmp/server.crt certs/server/server.crt
docker compose cp step-ca:/tmp/server.key certs/server/server.key

ok "Step-CA ready. Device CA: certs/ca/root_ca.crt"

# ---- start services (except nginx) ------------------------------------------
# nginx を後で起動することで certbot standalone がポート 80 を使える

step "Starting services (nginx starts after Let's Encrypt)..."
docker compose up -d \
    postgres influxdb emqx minio grafana core-api ingestion-service alert-service mailhog
ok "Services started."

# ---- wait for health --------------------------------------------------------

step "Waiting for services to become healthy (up to 120 seconds)..."

wait_healthy() {
    local svc=$1
    local deadline=$(( $(date +%s) + 120 ))
    printf "    Waiting for %s" "$svc"
    while true; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "")
        if [[ "$status" == "healthy" ]]; then echo -e " ${GREEN}healthy${NC}"; return 0; fi
        local state
        state=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "")
        if [[ "$state" == "exited" ]]; then echo -e " ${RED}exited (check: docker logs $svc)${NC}"; return 1; fi
        if (( $(date +%s) >= deadline )); then echo -e " ${YELLOW}timeout${NC}"; return 1; fi
        sleep 3; printf "."
    done
}

for svc in postgres influxdb step-ca emqx; do
    wait_healthy "$svc" || true
done

if ! wait_healthy "core-api"; then
    fail "core-api did not become healthy. Check: docker compose logs core-api"
fi

# ---- Let's Encrypt ----------------------------------------------------------

step "Obtaining Let's Encrypt certificate for ${PLATFORM_DOMAIN}..."

# DNS 疎通確認（警告のみ、ブロックしない）
if command -v dig &>/dev/null; then
    RESOLVED_IP=$(dig +short "${PLATFORM_DOMAIN}" 2>/dev/null | tail -1 || echo "")
    if [[ -z "$RESOLVED_IP" ]]; then
        warn "DNS lookup for ${PLATFORM_DOMAIN} returned nothing."
        warn "GoDaddy の A レコード設定が反映されていることを確認してください。"
    else
        ok "DNS: ${PLATFORM_DOMAIN} → ${RESOLVED_IP}"
    fi
fi

sudo certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email "${CERTBOT_EMAIL}" \
    -d "${PLATFORM_DOMAIN}"

ok "Let's Encrypt certificate obtained."

# nginx 用: Let's Encrypt 証明書を専用ファイルにコピー
# （emqx は step-ca 証明書を引き続き使用）
CERT_PATH="/etc/letsencrypt/live/${PLATFORM_DOMAIN}"
PROJ_ROOT="$(pwd)"
sudo cp "${CERT_PATH}/fullchain.pem" "${PROJ_ROOT}/certs/server/nginx.crt"
sudo cp "${CERT_PATH}/privkey.pem"   "${PROJ_ROOT}/certs/server/nginx.key"
sudo chown "$(id -u):$(id -g)" \
    "${PROJ_ROOT}/certs/server/nginx.crt" \
    "${PROJ_ROOT}/certs/server/nginx.key"

# ---- docker-compose.override.yml -------------------------------------------

step "Creating docker-compose.override.yml for AWS..."
cat > docker-compose.override.yml <<EOF
# AWS 用オーバーライド — install-aws.sh が生成 / git に含めないこと
services:
  nginx:
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ${PROJ_ROOT}/certs/server/nginx.crt:/etc/nginx/certs/server.crt:ro
      - ${PROJ_ROOT}/certs/server/nginx.key:/etc/nginx/certs/server.key:ro
      - ${PROJ_ROOT}/certs/ca/root_ca.crt:/etc/nginx/certs/root_ca.crt:ro
      - ./admin-ui:/usr/share/nginx/admin-ui:ro
  grafana:
    environment:
      GF_SERVER_ROOT_URL: "https://${PLATFORM_DOMAIN}/grafana/"
EOF
ok "docker-compose.override.yml created."

# ---- certbot auto-renewal hook ----------------------------------------------

step "Setting up certbot auto-renewal hook..."
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/iotpf-nginx.sh > /dev/null <<EOF
#!/bin/bash
# IoT Platform: Let's Encrypt 更新後に nginx 用証明書を更新する
set -e
CERT_PATH="/etc/letsencrypt/live/${PLATFORM_DOMAIN}"
PROJ_ROOT="${PROJ_ROOT}"
cp "\${CERT_PATH}/fullchain.pem" "\${PROJ_ROOT}/certs/server/nginx.crt"
cp "\${CERT_PATH}/privkey.pem"   "\${PROJ_ROOT}/certs/server/nginx.key"
docker compose -f "\${PROJ_ROOT}/docker-compose.yml" \
               -f "\${PROJ_ROOT}/docker-compose.override.yml" \
               restart nginx
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/iotpf-nginx.sh
ok "Renewal hook installed (/etc/letsencrypt/renewal-hooks/deploy/iotpf-nginx.sh)."

# ---- start nginx ------------------------------------------------------------

step "Starting nginx with Let's Encrypt certificate..."
docker compose up -d nginx
if ! wait_healthy "nginx"; then
    fail "nginx did not become healthy. Check: docker compose logs nginx"
fi

# ---- bootstrap platform admin account ----------------------------------------

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
  |  IoT Platform is running on AWS!                        |
  +---------------------------------------------------------+
  Admin / Login  https://${PLATFORM_DOMAIN}/admin/
                 ${PLATFORM_ADMIN_EMAIL:-admin@platform.local}
                 (password in .env → PLATFORM_ADMIN_PASSWORD)
  Grafana        https://${PLATFORM_DOMAIN}/grafana/
  MQTT (mTLS)    mqtts://${PLATFORM_DOMAIN}:8883

  MailHog   http://127.0.0.1:8025  (SSH tunnel: ssh -L 8025:localhost:8025 ...)
  InfluxDB  http://127.0.0.1:8086  (SSH tunnel required)

  Device CA: certs/ca/root_ca.crt  (step-ca 発行、デバイスに配布)
  TLS (Web): Let's Encrypt (自動更新済み)
  +---------------------------------------------------------+
  Credentials are in .env (chmod 600, keep it private)
  docker-compose.override.yml は AWS 専用 — git に含めないこと
  +---------------------------------------------------------+

  Next steps:
    1. Open https://${PLATFORM_DOMAIN}/admin/ and log in
    2. Create a tenant
    3. Provision your first device using the bootstrap token

  To stop:      docker compose down
  To view logs: docker compose logs -f
  Cert renew:   sudo certbot renew --dry-run  (テスト)
${NC}"
