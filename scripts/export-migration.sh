#!/bin/bash
# export-migration.sh
# IoT Platform migration package creator (Ubuntu/Linux 版)
# 使い方: bash scripts/export-migration.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="${PROJECT_DIR}/iot-platform-migration"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo ""
echo -e "${CYAN}===== IoT Platform Migration Export =====${NC}"
echo "Output: $OUT_DIR"
echo ""

# Docker 確認
docker info > /dev/null 2>&1 || { echo "Docker が起動していません。"; exit 1; }

# 出力フォルダ初期化
if [[ -d "$OUT_DIR" ]]; then
  echo "[!] 既存の migration フォルダを削除中..."
  rm -rf "$OUT_DIR"
fi
mkdir -p "$OUT_DIR/volumes"

# --- 1. Docker ボリュームエクスポート ---
echo -e "${YELLOW}[1/5] Docker ボリュームをエクスポート中...${NC}"
volumes=(postgres_data influxdb_data grafana_data step_ca_data emqx_data minio_data)

for vol in "${volumes[@]}"; do
  full="iot-platform_${vol}"
  echo "  - $full ..."
  if docker run --rm \
      -v "${full}:/data" \
      -v "${OUT_DIR}/volumes:/backup" \
      alpine sh -c "tar czf /backup/${vol}.tar.gz -C /data . 2>/dev/null"; then
    size=$(du -sh "${OUT_DIR}/volumes/${vol}.tar.gz" 2>/dev/null | cut -f1)
    echo -e "    ${GREEN}OK ($size)${NC}"
  else
    echo "    FAILED (continuing)" >&2
  fi
done

# --- 2. TLS 証明書コピー ---
echo -e "${YELLOW}[2/5] TLS 証明書をコピー中...${NC}"
certs_dir="${PROJECT_DIR}/certs"
if [[ -d "$certs_dir" ]]; then
  cp -r "$certs_dir" "${OUT_DIR}/certs"
  echo -e "  ${GREEN}OK${NC}"
else
  echo "  certs/ が見つかりません (skip)" >&2
fi

# --- 3. シミュレータデバイス証明書コピー ---
echo -e "${YELLOW}[3/5] シミュレータデバイス証明書をコピー中...${NC}"
sim_certs_dir="${PROJECT_DIR}/simulator/certs"
if [[ -d "$sim_certs_dir" ]]; then
  cp -r "$sim_certs_dir" "${OUT_DIR}/simulator-certs"
  count=$(find "${OUT_DIR}/simulator-certs" -type f | wc -l)
  echo -e "  ${GREEN}OK ($count files)${NC}"
else
  echo "  シミュレータ証明書なし (skip)"
fi

# --- 4. .env コピー ---
echo -e "${YELLOW}[4/5] .env をコピー中...${NC}"
env_file="${PROJECT_DIR}/.env"
if [[ -f "$env_file" ]]; then
  cp "$env_file" "${OUT_DIR}/.env"
  echo -e "  ${GREEN}OK${NC}"
else
  echo "  .env が見つかりません" >&2
fi

# --- 5. コードベース ZIP ---
echo -e "${YELLOW}[5/5] コードベースを圧縮中...${NC}"
zip_path="${OUT_DIR}/iot-platform-code.zip"
# migration フォルダ・git・build 成果物を除外
(cd "$PROJECT_DIR" && zip -qr "$zip_path" . \
  --exclude "iot-platform-migration/*" \
  --exclude ".git/*" \
  --exclude "simulator/build/*" \
  --exclude "simulator/dist/*" \
  --exclude "*.egg-info/*" \
  --exclude "__pycache__/*" \
  --exclude "*.pyc" \
  --exclude ".env")
echo -e "  ${GREEN}OK${NC}"

# --- 完了 ---
echo ""
echo -e "${CYAN}===== エクスポート完了 =====${NC}"
echo "パッケージフォルダ: $OUT_DIR"
echo ""
echo -e "${YELLOW}次のステップ:${NC}"
echo "  1. 'iot-platform-migration' フォルダを新しい PC にコピー (USB / 共有フォルダ等)"
echo "  2. 新しい PC で:"
echo "     a. Docker をインストール"
echo "     b. iot-platform-code.zip を展開"
echo "     c. bash scripts/import-migration.sh <path/to/iot-platform-migration>"
echo ""
echo -e "${CYAN}パッケージ内容:${NC}"
find "$OUT_DIR" -type f | while read -r f; do
  rel="${f#${OUT_DIR}/}"
  size=$(du -sh "$f" | cut -f1)
  printf "  %-50s %s\n" "$rel" "$size"
done
