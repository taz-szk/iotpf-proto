#!/bin/bash
set -e

source "$(dirname "$0")/../.env"

EMQX_API="http://localhost:18083/api/v5"
AUTH="admin:${EMQX_DASHBOARD_PASSWORD}"

echo "=== EMQX Rule Engine 設定 ==="

echo "[1/3] 既存ルール確認..."
curl -sf -u "${AUTH}" "${EMQX_API}/rules" > /dev/null && echo "  EMQX API接続OK" || { echo "  EMQX API接続失敗"; exit 1; }

echo "[2/3] ingestion-service用ルール作成..."
curl -sf -X POST "${EMQX_API}/rules" \
  -u "${AUTH}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"telemetry_ingest\",
    \"sql\": \"SELECT topic, payload, clientid FROM \\\"/+/devices/+/telemetry\\\"\",
    \"actions\": [],
    \"enable\": true,
    \"description\": \"Telemetry ingestion rule (HTTP action configured via dashboard)\"
  }" > /dev/null 2>&1 && echo "  telemetryルール作成成功" || echo "  telemetryルール既存 or 失敗"

echo "[3/3] statusルール作成..."
curl -sf -X POST "${EMQX_API}/rules" \
  -u "${AUTH}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"status_ingest\",
    \"sql\": \"SELECT topic, payload, clientid FROM \\\"/+/devices/+/status\\\"\",
    \"actions\": [],
    \"enable\": true,
    \"description\": \"Device status rule (HTTP action configured via dashboard)\"
  }" > /dev/null 2>&1 && echo "  statusルール作成成功" || echo "  statusルール既存 or 失敗"

echo ""
echo "=== 設定完了 ==="
echo "次のステップ: EMQXダッシュボード(http://localhost:18083)でHTTP Actionを設定"
echo "  telemetry → POST http://ingestion-service:8001/ingest"
echo "  status    → POST http://ingestion-service:8001/ingest"
