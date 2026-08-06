#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

source "${PROJECT_DIR}/.env"

echo "=== IoTプラットフォーム 初回セットアップ ==="

mkdir -p "${PROJECT_DIR}/certs/ca"
mkdir -p "${PROJECT_DIR}/certs/server"
mkdir -p "${PROJECT_DIR}/step-ca/data"

# Step-CA を起動（DOCKER_STEPCA_INIT_* 環境変数で自動初期化）
echo "[1/4] Step-CA起動・初期化..."
docker compose up -d step-ca

echo "Step-CA起動待機中..."
until docker compose exec -T step-ca \
  step ca health --ca-url=https://localhost:9000 \
  --root=/home/step/certs/root_ca.crt 2>/dev/null; do
  sleep 3
done

echo "[2/4] ルートCA証明書をコピー..."
docker compose cp step-ca:/home/step/certs/root_ca.crt \
  "${PROJECT_DIR}/certs/ca/root_ca.crt"

echo "[3/4] サーバー証明書生成..."
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

docker compose cp step-ca:/tmp/server.crt "${PROJECT_DIR}/certs/server/server.crt"
docker compose cp step-ca:/tmp/server.key "${PROJECT_DIR}/certs/server/server.key"

echo "[4/4] セットアップ完了"
echo "次のステップ: docker compose up -d"
