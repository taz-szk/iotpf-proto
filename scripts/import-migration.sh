#!/bin/bash
# import-migration.sh
# IoT Platform migration importer (Ubuntu/Linux 版)
#
# 使い方:
#   1. 新しい PC に Docker をインストール
#   2. iot-platform-code.zip を展開
#   3. 展開したフォルダで:
#      bash scripts/import-migration.sh /path/to/iot-platform-migration
#
set -euo pipefail

MIGRATION_DIR="${1:-}"

if [[ -z "$MIGRATION_DIR" ]]; then
  echo "使い方: bash scripts/import-migration.sh <migration フォルダのパス>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

echo ""
echo -e "${CYAN}===== IoT Platform Migration Import =====${NC}"
echo "Migration フォルダ: $MIGRATION_DIR"
echo "プロジェクトフォルダ: $PROJECT_DIR"
echo ""

[[ -d "$MIGRATION_DIR" ]] || { echo -e "${RED}Migration フォルダが見つかりません: $MIGRATION_DIR${NC}"; exit 1; }
docker info > /dev/null 2>&1 || { echo -e "${RED}Docker が起動していません。${NC}"; exit 1; }

# --- 1. .env ---
echo -e "${YELLOW}[1/5] .env をコピー中...${NC}"
env_src="${MIGRATION_DIR}/.env"
env_dst="${PROJECT_DIR}/.env"
if [[ -f "$env_src" ]]; then
  if [[ -f "$env_dst" ]]; then
    cp "$env_dst" "${env_dst}.bak"
    echo "  既存の .env を .env.bak としてバックアップ"
  fi
  cp "$env_src" "$env_dst"
  chmod 600 "$env_dst"
  echo -e "  ${GREEN}OK${NC}"
else
  echo "  .env が migration フォルダに見つかりません" >&2
fi

# --- 2. TLS 証明書 ---
echo -e "${YELLOW}[2/5] TLS 証明書をコピー中...${NC}"
certs_src="${MIGRATION_DIR}/certs"
certs_dst="${PROJECT_DIR}/certs"
if [[ -d "$certs_src" ]]; then
  [[ -d "$certs_dst" ]] && rm -rf "$certs_dst"
  cp -r "$certs_src" "$certs_dst"
  echo -e "  ${GREEN}OK${NC}"
else
  echo "  certs/ が migration フォルダに見つかりません" >&2
fi

# --- 3. Docker ボリュームインポート ---
echo -e "${YELLOW}[3/5] Docker ボリュームをインポート中...${NC}"
volumes=(postgres_data influxdb_data grafana_data step_ca_data emqx_data minio_data)

for vol in "${volumes[@]}"; do
  full="iot-platform_${vol}"
  tar_file="${MIGRATION_DIR}/volumes/${vol}.tar.gz"

  if [[ ! -f "$tar_file" ]]; then
    echo "  $vol.tar.gz が見つかりません (skip)"
    continue
  fi

  echo "  - $full ..."
  docker volume create "$full" > /dev/null
  if docker run --rm \
      -v "${full}:/data" \
      -v "${MIGRATION_DIR}/volumes:/backup" \
      alpine sh -c "cd /data && rm -rf ./* ./..?* ./.[!.]* 2>/dev/null; tar xzf /backup/${vol}.tar.gz -C /data"; then
    echo -e "    ${GREEN}OK${NC}"
  else
    echo "    FAILED" >&2
  fi
done

# --- 4. シミュレータデバイス証明書 ---
echo -e "${YELLOW}[4/5] シミュレータデバイス証明書をコピー中...${NC}"
sim_src="${MIGRATION_DIR}/simulator-certs"
sim_dst="${PROJECT_DIR}/simulator/certs"
if [[ -d "$sim_src" ]]; then
  [[ -d "$sim_dst" ]] && rm -rf "$sim_dst"
  cp -r "$sim_src" "$sim_dst"
  echo -e "  ${GREEN}OK${NC}"
else
  echo "  シミュレータ証明書なし (skip)"
fi

# --- 5. コンテナ起動 ---
echo -e "${YELLOW}[5/5] コンテナを起動中...${NC}"
cd "$PROJECT_DIR"
if docker compose up -d; then
  echo -e "  ${GREEN}OK${NC}"
else
  echo "  docker compose up に失敗しました。docker compose logs で確認してください。" >&2
fi

# --- 完了 ---
echo ""
echo -e "${CYAN}===== インポート完了 =====${NC}"
echo ""
echo -e "${YELLOW}確認事項:${NC}"
echo ""
echo "  [A] 元の PC と IP / ホスト名が変わった場合:"
echo "      - .env の PLATFORM_DOMAIN を新しい IP またはホスト名に変更"
echo "      - サーバー TLS 証明書を再生成:"
echo "        bash scripts/setup.sh"
echo "      - nginx を再起動:"
echo "        docker compose restart nginx"
echo ""
echo "  [B] ファイアウォール設定を確認:"
echo "      443  (HTTPS — Web UI & API)"
echo "      8883 (MQTTS — デバイス接続)"
echo "      8025 (MailHog — 任意)"
echo ""
echo "  [C] シミュレータ:"
echo "      simulator/certs/ にデバイス証明書が復元されました。"
echo "      シミュレータが別マシンの場合は exe と certs/ フォルダをそちらへコピー。"
echo ""
