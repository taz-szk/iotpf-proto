#!/bin/bash
# 稼働中コンテナイメージの脆弱性スキャン（Trivy、Critical/High・修正版ありのみ）を実行し、
# 結果をSlackへ通知する。GitHub Actions等の外部CIは使わず、cronでの定期実行を想定。
#
# 事前準備:
#   1. Slack Incoming Webhookを作成し、URLを.envに追記する:
#      VULN_SCAN_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
#   2. crontabに登録する（例: 毎週月曜7時）:
#      0 7 * * 1 /path/to/iotpf-proto/scripts/vuln-scan.sh >> /path/to/iotpf-proto/vuln-scan.log 2>&1
set -e

cd "$(dirname "$0")/.."
source .env

: "${VULN_SCAN_SLACK_WEBHOOK_URL:?VULN_SCAN_SLACK_WEBHOOK_URLを.envに設定してください}"

if ! command -v jq &>/dev/null; then
    sudo apt-get install -y jq
fi

IMAGES=$(docker compose config --images | sort -u)
IMAGE_COUNT=$(echo "$IMAGES" | wc -l)

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

TOTAL_FINDINGS=0
REPORT_FILE="$TMPDIR/report.txt"
: > "$REPORT_FILE"

for image in $IMAGES; do
    safe_name=$(echo "$image" | tr '/:' '__')
    outfile="$TMPDIR/${safe_name}.json"

    if ! docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v trivy-cache:/root/.cache/ \
        aquasec/trivy image \
        --severity CRITICAL,HIGH --ignore-unfixed \
        --format json --quiet -o "$outfile" "$image"; then
        echo "WARN: trivyの実行に失敗しました: $image" >&2
        continue
    fi

    count=$(jq '[.Results[]?.Vulnerabilities[]?] | length' "$outfile")
    if [ "$count" -gt 0 ]; then
        TOTAL_FINDINGS=$((TOTAL_FINDINGS + count))
        {
            printf '*%s*（%s件）\n' "$image" "$count"
            jq -r '.Results[]?.Vulnerabilities[]? |
                "- \(.PkgName) \(.InstalledVersion) → \(.VulnerabilityID)（\(.Severity)、修正版: \(.FixedVersion // "-")）"' "$outfile"
            printf '\n'
        } >> "$REPORT_FILE"
    fi
done

DATE=$(date '+%Y-%m-%d %H:%M %Z')

if [ "$TOTAL_FINDINGS" -eq 0 ]; then
    TEXT=$(printf '🔍 脆弱性スキャン実施（%s）\n対象%s イメージ、Critical/High（修正版あり）の検出なし' "$DATE" "$IMAGE_COUNT")
else
    TEXT=$(printf '🚨 脆弱性スキャン実施（%s）\n対象%s イメージ、Critical/High（修正版あり）%s件検出\n\n%s' \
        "$DATE" "$IMAGE_COUNT" "$TOTAL_FINDINGS" "$(cat "$REPORT_FILE")")
fi

curl -sf -X POST -H 'Content-Type: application/json' \
    --data "$(jq -n --arg text "$TEXT" '{text: $text}')" \
    "$VULN_SCAN_SLACK_WEBHOOK_URL" > /dev/null

echo "通知しました（検出件数: ${TOTAL_FINDINGS}）"
