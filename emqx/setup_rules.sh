#!/bin/sh
# EMQX ルールエンジン設定スクリプト
# 用途: docker compose up 後に一度実行する（ボリューム削除時の復元に使用）
# 実行: docker exec emqx sh /opt/emqx/etc/setup_rules.sh <dashboard_password>

BASE="http://localhost:18083"
PASS="${1:-changeme_strong_password}"

echo "=== EMQX Rule Engine Setup ==="
echo "Logging in..."

TOKEN=$(curl -sf -X POST "$BASE/api/v5/login" \
  -H 'Content-Type: application/json' \
  --data-binary '{"username":"admin","password":"'"$PASS"'"}' | \
  grep -o '"token":"[^"]*"' | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
  echo "ERROR: Login failed. Check the password."
  exit 1
fi
echo "Login OK"

# --- HTTP Connector ---
echo ""
echo "Creating HTTP connector to ingestion-service..."
curl -sf -X POST "$BASE/api/v5/connectors" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary '{
    "name": "ingestion_connector",
    "type": "http",
    "enable": true,
    "url": "http://ingestion-service:8001",
    "connect_timeout": "15s",
    "pool_type": "random",
    "pool_size": 4
  }' > /dev/null && echo "Connector created" || echo "Connector may already exist (OK)"

# --- HTTP Action ---
echo ""
echo "Creating HTTP action..."
curl -sf -X POST "$BASE/api/v5/actions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary '{
    "name": "ingestion_action",
    "type": "http",
    "enable": true,
    "connector": "ingestion_connector",
    "parameters": {
      "method": "post",
      "path": "/ingest",
      "headers": {"content-type": "application/json"},
      "body": "{\"tenant_id\":\"${tenant_id}\",\"device_id\":\"${device_id}\",\"payload\":${payload},\"topic_type\":\"${topic_type}\"}"
    }
  }' > /dev/null && echo "Action created" || echo "Action may already exist (OK)"

# --- Rule ---
echo ""
echo "Creating rule..."
curl -sf -X POST "$BASE/api/v5/rules" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary '{
    "name": "telemetry_and_status_ingest",
    "enable": true,
    "sql": "SELECT nth(2, tokens(topic, '"'"'/'"'"')) as tenant_id, nth(4, tokens(topic, '"'"'/'"'"')) as device_id, payload, CASE WHEN topic =~ '"'"'/+/devices/+/telemetry'"'"' THEN '"'"'telemetry'"'"' ELSE '"'"'status'"'"' END as topic_type FROM \"#\" WHERE topic =~ '"'"'/+/devices/+/telemetry'"'"' OR topic =~ '"'"'/+/devices/+/status'"'"'",
    "actions": ["http:ingestion_action"]
  }' > /dev/null && echo "Rule created" || echo "Rule may already exist (OK)"

echo ""
echo "=== Setup complete ==="
echo "Verify at: http://localhost:18083 (admin / $PASS)"
