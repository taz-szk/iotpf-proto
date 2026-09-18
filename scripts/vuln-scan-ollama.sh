#!/bin/bash
# Ollama専用インスタンス上のコンテナイメージの脆弱性スキャン（Trivy、Critical/High・
# 修正版ありのみ）を実行し、結果をSlackへ通知する。vuln-scan.sh（メインインスタンス側）
# と同じ仕組みだが、docker-compose.ymlに含まれないOllama専用インスタンス向けに、
# 対象イメージを直接指定する版。
#
# 事前準備:
#   1. このファイルをOllamaインスタンスの~/vuln-scan-ollama.shあたりに配置する
#   2. メインインスタンスと同じSlack Webhook URLを環境変数として設定する
#      （このインスタンスにはiotpf-protoの.envが無いため、直接エクスポートするか
#      別途.envファイルを用意してsourceする）:
#      export VULN_SCAN_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
#   3. crontabに登録する（例: 毎週土曜7時、メインインスタンス側と揃える）:
#      0 7 * * 6 VULN_SCAN_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz /home/ubuntu/vuln-scan-ollama.sh >> /home/ubuntu/vuln-scan-ollama.log 2>&1
set -e

: "${VULN_SCAN_SLACK_WEBHOOK_URL:?VULN_SCAN_SLACK_WEBHOOK_URLを環境変数に設定してください}"

if ! command -v jq &>/dev/null; then
    sudo apt-get install -y jq
fi

IMAGES="ollama/ollama:latest"
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
        -v "$TMPDIR:$TMPDIR" \
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
    TEXT=$(printf '🔍 [Ollamaインスタンス] 脆弱性スキャン実施（%s）\n対象%s イメージ、Critical/High（修正版あり）の検出なし' "$DATE" "$IMAGE_COUNT")
else
    TEXT=$(printf '🚨 [Ollamaインスタンス] 脆弱性スキャン実施（%s）\n対象%s イメージ、Critical/High（修正版あり）%s件検出\n\n%s' \
        "$DATE" "$IMAGE_COUNT" "$TOTAL_FINDINGS" "$(cat "$REPORT_FILE")")
fi

curl -sf -X POST -H 'Content-Type: application/json' \
    --data "$(jq -n --arg text "$TEXT" '{text: $text}')" \
    "$VULN_SCAN_SLACK_WEBHOOK_URL" > /dev/null

echo "通知しました（検出件数: ${TOTAL_FINDINGS}）"
