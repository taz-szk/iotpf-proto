#!/bin/bash
# vuln-scan.shから呼ばれる自動remediation。
# 稼働中イメージをpull/rebuildし、変化があったものだけ再作成する(docker compose up -dは
# イメージが変わっていなければコンテナを作り直さないため、無変化なら実質no-op)。
#
# 対象:
#   - ローカルビルドの3サービス(core-api/ingestion-service/alert-service):
#     自分たちのDockerfile/requirements.txtなので毎回 --no-cache --pull で再ビルド
#   - floatingタグの外部イメージ(step-ca/postgres/influxdb/emqx/minio/nginx/mailhog):
#     pullしてupstreamの新ビルドがあれば反映
#
# 対象外:
#   - grafana: GRAFANA_IMAGEで固定バージョン運用。タグを上げる判断は動作確認が必要な
#     別作業として扱うため、ここでは触らない([[project_vuln_scan_weekly_workflow]]参照)。
#
# 1サービスの失敗が他を止めないよう、set -e は使わない。

set -uo pipefail

cd "$(dirname "$0")/.."

LOCAL_SERVICES="core-api ingestion-service alert-service"
REGISTRY_SERVICES="step-ca postgres influxdb emqx minio nginx mailhog"

echo "=== vuln-remediate: $(date '+%Y-%m-%d %H:%M %Z') ==="

update_if_changed() {
    local svc="$1"
    local before after
    before=$(docker compose images -q "$svc" 2>/dev/null)
    docker compose up -d "$svc" >/dev/null 2>&1
    after=$(docker compose images -q "$svc" 2>/dev/null)
    if [ "$before" != "$after" ]; then
        echo "updated: $svc ($before -> $after)"
    else
        echo "unchanged: $svc"
    fi
}

for svc in $LOCAL_SERVICES; do
    echo "--- build: $svc ---"
    if docker compose build --no-cache --pull "$svc"; then
        update_if_changed "$svc"
    else
        echo "WARN: build失敗: $svc" >&2
    fi
done

for svc in $REGISTRY_SERVICES; do
    echo "--- pull: $svc ---"
    if docker compose pull "$svc"; then
        update_if_changed "$svc"
    else
        echo "WARN: pull失敗: $svc" >&2
    fi
done

echo "=== vuln-remediate: 完了 ==="
