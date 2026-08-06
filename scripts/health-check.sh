#!/bin/bash
set -e

source "$(dirname "$0")/../.env"

PASS=0
FAIL=0

check() {
    local name="$1"
    local cmd="$2"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "  [OK] $name"
        ((PASS++)) || true
    else
        echo "  [NG] $name"
        ((FAIL++)) || true
    fi
}

echo "=== IoTプラットフォーム ヘルスチェック ==="
echo ""

echo "[PostgreSQL]"
check "接続確認" \
    "docker exec postgres pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"
check "テナントテーブル存在確認" \
    "docker exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c 'SELECT 1 FROM tenants LIMIT 1'"

echo ""
echo "[InfluxDB]"
check "ping確認" \
    "curl -sf http://localhost:8086/ping"
check "認証確認" \
    "curl -sf -H 'Authorization: Token ${INFLUXDB_ADMIN_TOKEN}' http://localhost:8086/api/v2/orgs"

echo ""
echo "[EMQX]"
check "ノード状態確認" \
    "docker exec emqx emqx ping"
check "管理API確認" \
    "curl -sf -u admin:${EMQX_DASHBOARD_PASSWORD} http://localhost:18083/api/v5/status"

echo ""
echo "[MinIO]"
check "ヘルスチェック" \
    "curl -sf http://localhost:9000/minio/health/live"

echo ""
echo "[Grafana]"
check "ヘルスチェック" \
    "curl -sf http://localhost:3000/api/health"

echo ""
echo "[Nginx/HTTPS]"
check "HTTPSアクセス確認" \
    "curl -sf -k https://localhost/api/health"

echo ""
echo "=== 結果: ${PASS}件成功 / ${FAIL}件失敗 ==="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
