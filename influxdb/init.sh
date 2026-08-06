#!/bin/bash
set -e

echo "InfluxDB初期設定を開始..."

# InfluxDBが起動するまで待機
until curl -sf http://influxdb:8086/ping; do
  echo "InfluxDB起動待機中..."
  sleep 2
done

# セットアップ済みか確認
if influx setup check --host http://influxdb:8086 2>/dev/null; then
  echo "InfluxDB既にセットアップ済み、スキップします"
  exit 0
fi

# 初期セットアップ
influx setup \
  --host http://influxdb:8086 \
  --username "${INFLUXDB_ADMIN_USER}" \
  --password "${INFLUXDB_ADMIN_PASSWORD}" \
  --token "${INFLUXDB_ADMIN_TOKEN}" \
  --org "${INFLUXDB_ORG}" \
  --bucket "${INFLUXDB_BUCKET}" \
  --retention 0 \
  --force

echo "InfluxDB初期設定完了"
