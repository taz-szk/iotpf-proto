#!/bin/bash
# MQTTSの疎通テスト（mosquitto_pub/subが必要）
# インストール: apt install mosquitto-clients

source "$(dirname "$0")/../.env"

CERTS_DIR="$(dirname "$0")/../certs"

echo "=== MQTTSテスト ==="

echo "テスト用トピックへpublish..."
mosquitto_pub \
  -h localhost \
  -p 8883 \
  --cafile "${CERTS_DIR}/ca/root_ca.crt" \
  --cert "${CERTS_DIR}/server/server.crt" \
  --key "${CERTS_DIR}/server/server.key" \
  -t "/test/devices/device001/telemetry" \
  -m '{"temperature": 25.3}' \
  --tls-version tlsv1.2 \
  -d

echo "MQTTSテスト完了"
