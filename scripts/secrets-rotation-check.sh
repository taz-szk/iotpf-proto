#!/bin/bash
# .envで管理しているプラットフォーム単位のシークレットについて、ローテーション周期を
# 超えているものをSlackに通知する。cronでの定期実行を想定(例: 毎月1日)。
#
# 対象はテナント単位でDB管理されているシークレット(SORACOM Auth Key、テナントごとの
# Slack Webhook等)は含まない。.envのプラットフォーム単位シークレットのみ。
#
# 事前準備:
#   1. scripts/secrets-rotation-state.example.json を scripts/secrets-rotation-state.json
#      にコピーし、実際の最終ローテーション日に書き換える(このファイルはシークレットの
#      値を含まないが、運用状態なのでgitignore対象)。
#   2. Slack Incoming WebhookのURLを.envに追記する(vuln-scan.shと共用):
#      VULN_SCAN_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
#   3. crontabに登録する(例: 毎月1日9時):
#      0 9 1 * * /path/to/iotpf-proto/scripts/secrets-rotation-check.sh >> /path/to/iotpf-proto/secrets-rotation-check.log 2>&1
set -e

cd "$(dirname "$0")/.."
source .env

: "${VULN_SCAN_SLACK_WEBHOOK_URL:?VULN_SCAN_SLACK_WEBHOOK_URLを.envに設定してください}"

STATE_FILE="scripts/secrets-rotation-state.json"
if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: $STATE_FILE が見つかりません。scripts/secrets-rotation-state.example.jsonをコピーして作成してください。" >&2
    exit 1
fi

if ! command -v jq &>/dev/null; then
    sudo apt-get install -y jq
fi

TODAY_EPOCH=$(date +%s)
OVERDUE_FILE=$(mktemp)
OK_COUNT=0
trap 'rm -f "$OVERDUE_FILE"' EXIT

for key in $(jq -r 'keys[]' "$STATE_FILE"); do
    interval_days=$(jq -r --arg k "$key" '.[$k].interval_days' "$STATE_FILE")
    last_rotated=$(jq -r --arg k "$key" '.[$k].last_rotated' "$STATE_FILE")

    last_epoch=$(date -d "$last_rotated" +%s 2>/dev/null) || {
        echo "WARN: ${key}のlast_rotated(${last_rotated})の日付解析に失敗、スキップ" >&2
        continue
    }

    days_since=$(( (TODAY_EPOCH - last_epoch) / 86400 ))

    if [ "$days_since" -ge "$interval_days" ]; then
        overdue_by=$(( days_since - interval_days ))
        printf -- '- %s（最終: %s、周期%s日、%s日超過）\n' "$key" "$last_rotated" "$interval_days" "$overdue_by" >> "$OVERDUE_FILE"
    else
        OK_COUNT=$((OK_COUNT + 1))
    fi
done

DATE=$(date '+%Y-%m-%d %H:%M %Z')
OVERDUE_COUNT=$(wc -l < "$OVERDUE_FILE")

if [ "$OVERDUE_COUNT" -eq 0 ]; then
    TEXT=$(printf '🔑 シークレットローテーション確認（%s）\n✅ %s件すべて周期内' "$DATE" "$OK_COUNT")
else
    TEXT=$(printf '🔑 シークレットローテーション確認（%s）\n⚠️ %s件が期限超過\n\n%s\n✅ 他%s件は周期内' \
        "$DATE" "$OVERDUE_COUNT" "$(cat "$OVERDUE_FILE")" "$OK_COUNT")
fi

curl -sf -X POST -H 'Content-Type: application/json' \
    --data "$(jq -n --arg text "$TEXT" '{text: $text}')" \
    "$VULN_SCAN_SLACK_WEBHOOK_URL" > /dev/null

echo "通知しました（期限超過: ${OVERDUE_COUNT}件、周期内: ${OK_COUNT}件）"
