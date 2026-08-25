#!/bin/bash
# IoT Platform — Ubuntu クリーンインストールスクリプト
#
# 使い方:
#   bash scripts/install-ubuntu.sh [オプション]
#
# オプション:
#   --domain <名前>      プラットフォームドメイン (デフォルト: platform.local)
#   --with-simulator     Python SDK とシミュレータ依存関係もインストール
#   --skip-docker        Docker インストールをスキップ (既にインストール済みの場合)
#   --help               このヘルプを表示
#
# 実行ユーザー: sudo 権限を持つ一般ユーザー (root での実行は非推奨)
#
set -euo pipefail

# ─── 定数 ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PLATFORM_DOMAIN="platform.local"
WITH_SIMULATOR=false
SKIP_DOCKER=false

# ─── 引数解析 ─────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --domain)          PLATFORM_DOMAIN="$2"; shift 2 ;;
    --with-simulator)  WITH_SIMULATOR=true;  shift ;;
    --skip-docker)     SKIP_DOCKER=true;     shift ;;
    --help)
      grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "不明なオプション: $1"; exit 1 ;;
  esac
done

# ─── ロガー ───────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERR ]${NC}  $*" >&2; }
log_step()  { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }

die() { log_error "$*"; exit 1; }

# ─── 前提チェック ─────────────────────────────────────────
check_prereqs() {
  log_step "環境チェック"

  # root で実行されていないことを確認
  if [[ "$EUID" -eq 0 ]]; then
    die "root ユーザーでは実行しないでください。sudo 権限を持つ一般ユーザーで実行してください。"
  fi

  # Ubuntu 確認
  if ! grep -qi ubuntu /etc/os-release 2>/dev/null; then
    log_warn "Ubuntu が検出されませんでした。このスクリプトは Ubuntu 向けです。"
    read -rp "続行しますか? [y/N]: " ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 1
  fi

  # sudo 確認
  if ! sudo -v 2>/dev/null; then
    die "sudo 権限が必要です。"
  fi

  # openssl 確認
  command -v openssl &>/dev/null || die "openssl が見つかりません: sudo apt-get install -y openssl"

  log_ok "環境チェック完了"
}

# ─── Docker インストール ───────────────────────────────────
install_docker() {
  if $SKIP_DOCKER; then
    log_info "Docker インストールをスキップします (--skip-docker)"
    return
  fi

  if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    log_ok "Docker は既にインストール済み: $(docker --version | cut -d' ' -f1-3)"
    return
  fi

  log_step "Docker Engine をインストール中"
  sudo apt-get update -q
  sudo apt-get install -y -q ca-certificates curl gnupg lsb-release

  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

  sudo apt-get update -q
  sudo apt-get install -y -q \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

  sudo systemctl enable --now docker

  # 現在のユーザーを docker グループに追加
  sudo usermod -aG docker "$USER"
  log_ok "Docker インストール完了"
  log_warn "docker グループへの追加は次回ログイン後に有効になります。"
  log_warn "このスクリプト内では sudo docker を使用して続行します。"

  # 現セッションでは sudo が必要
  DOCKER="sudo docker"
}

# docker コマンドを決定
resolve_docker_cmd() {
  if [[ -z "${DOCKER:-}" ]]; then
    if groups | grep -qw docker; then
      DOCKER="docker"
    else
      DOCKER="sudo docker"
    fi
  fi
}

# ─── Python インストール ───────────────────────────────────
install_python() {
  # Python 3.11 以上を探す
  for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null \
        && "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
      PYTHON="$py"
      log_ok "Python 利用可能: $($PYTHON --version)"
      return
    fi
  done

  log_step "Python 3.12 をインストール中"
  sudo apt-get update -q
  sudo apt-get install -y -q python3.12 python3.12-venv python3-pip
  PYTHON="python3.12"
  log_ok "Python インストール完了: $($PYTHON --version)"
}

# ─── .env 生成 ─────────────────────────────────────────────
generate_env() {
  log_step ".env ファイルの準備"

  if [[ -f "$PROJECT_DIR/.env" ]]; then
    log_warn ".env が既に存在します。生成をスキップします。"
    return
  fi

  local influx_token; influx_token=$(openssl rand -hex 32)
  local jwt_secret;   jwt_secret=$(openssl rand -hex 32)
  local pg_pass;      pg_pass=$(openssl rand -base64 18 | tr -d '+/=')
  local emqx_pass;    emqx_pass=$(openssl rand -base64 18 | tr -d '+/=')
  local emqx_cookie;  emqx_cookie=$(openssl rand -base64 24 | tr -d '+/=')
  local minio_pass;   minio_pass=$(openssl rand -base64 18 | tr -d '+/=')
  local stepca_pass;  stepca_pass=$(openssl rand -base64 18 | tr -d '+/=')
  local grafana_pass; grafana_pass=$(openssl rand -base64 18 | tr -d '+/=')
  local influx_pass;  influx_pass=$(openssl rand -base64 18 | tr -d '+/=')
  local admin_pass;   admin_pass=$(openssl rand -base64 18 | tr -d '+/=')

  cat > "$PROJECT_DIR/.env" <<EOF
# install-ubuntu.sh が $(date -u +"%Y-%m-%dT%H:%M:%SZ") に自動生成
# 本番運用前に全パスワードを見直してください。

# PostgreSQL
POSTGRES_DB=iotplatform
POSTGRES_USER=iotadmin
POSTGRES_PASSWORD=${pg_pass}

# InfluxDB
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=${influx_pass}
INFLUXDB_ADMIN_TOKEN=${influx_token}
INFLUXDB_ORG=iotplatform
INFLUXDB_BUCKET=system

# EMQX
EMQX_DASHBOARD_PASSWORD=${emqx_pass}
EMQX_NODE_COOKIE=${emqx_cookie}

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=${minio_pass}

# Step-CA
STEP_CA_PASSWORD=${stepca_pass}

# Nginx / TLS
PLATFORM_DOMAIN=${PLATFORM_DOMAIN}

# Grafana
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=${grafana_pass}

# Core API
JWT_SECRET=${jwt_secret}

# Platform Admin (initial login at /admin/)
PLATFORM_ADMIN_EMAIL=admin@${PLATFORM_DOMAIN}
PLATFORM_ADMIN_PASSWORD=${admin_pass}

# Alert Service — SMTP（デフォルトは MailHog で受信確認）
SMTP_HOST=mailhog
SMTP_PORT=1025
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=alerts@iot-platform.local

# Alert/Ingestion Service
EVAL_INTERVAL_SEC=60
DEVICE_OFFLINE_THRESHOLD_SEC=180
EOF

  chmod 600 "$PROJECT_DIR/.env"
  log_ok ".env を生成しました (パーミッション 600)"
}

# ─── /etc/hosts 設定 ──────────────────────────────────────
setup_hosts() {
  log_step "/etc/hosts への追記"

  if grep -qF "$PLATFORM_DOMAIN" /etc/hosts; then
    log_ok "$PLATFORM_DOMAIN は /etc/hosts に既に存在します。"
    return
  fi

  echo "127.0.0.1  ${PLATFORM_DOMAIN}" | sudo tee -a /etc/hosts > /dev/null
  log_ok "追記完了: 127.0.0.1  ${PLATFORM_DOMAIN}"
}

# ─── TLS 証明書生成 ───────────────────────────────────────
run_setup_certs() {
  log_step "TLS 証明書を生成中（Step-CA）"
  cd "$PROJECT_DIR"
  bash scripts/setup.sh
  log_ok "証明書生成完了"
}

# ─── サービス起動 ─────────────────────────────────────────
start_services() {
  log_step "Docker サービスを起動中"
  cd "$PROJECT_DIR"
  $DOCKER compose up -d --build

  log_info "全サービスの起動を待機中（最大 3 分）..."
  local deadline=$(( $(date +%s) + 180 ))
  while (( $(date +%s) < deadline )); do
    local still_starting
    still_starting=$(
      $DOCKER compose ps --format '{{.Health}}' 2>/dev/null \
        | grep -c "starting" || true
    )
    if [[ "$still_starting" -eq 0 ]]; then
      log_ok "全サービスが起動しました"
      break
    fi
    log_info "起動待機中... (残り $((deadline - $(date +%s)))s)"
    sleep 8
  done
}

# ─── プラットフォーム管理者アカウント ─────────────────────
# platform_users テーブルは空のまま作られるだけで、最初のログインアカウントを
# 作る処理がどこにもない。core-api 自身の DB セッション/ハッシュ関数を使って
# 1件だけ作成する（/auth/login の検証と同じハッシュ形式になることを保証するため）。
# 既に同じメールのアカウントがあれば何もしない（再実行時にパスワードを壊さない）。
bootstrap_platform_admin() {
  log_step "プラットフォーム管理者アカウントを準備中"
  cd "$PROJECT_DIR"

  local admin_email admin_pass
  admin_email=$(grep ^PLATFORM_ADMIN_EMAIL "$PROJECT_DIR/.env" | cut -d= -f2-)
  admin_pass=$(grep ^PLATFORM_ADMIN_PASSWORD "$PROJECT_DIR/.env" | cut -d= -f2-)

  $DOCKER compose exec -T \
    -e PLATFORM_ADMIN_EMAIL="${admin_email}" \
    -e PLATFORM_ADMIN_PASSWORD="${admin_pass}" \
    core-api python3 - <<'PYEOF'
import os
from app.database import SessionLocal
from app.models.public import PlatformUser
from app.services.auth import hash_password

email = os.environ["PLATFORM_ADMIN_EMAIL"]
password = os.environ["PLATFORM_ADMIN_PASSWORD"]
with SessionLocal() as db:
    if db.query(PlatformUser).filter(PlatformUser.email == email).first():
        print(f"[skip] platform admin {email} already exists")
    else:
        db.add(PlatformUser(email=email, password_hash=hash_password(password)))
        db.commit()
        print(f"[ok] created platform admin {email}")
PYEOF

  log_ok "プラットフォーム管理者: ${admin_email}"
}

# ─── EMQX ルール設定 ──────────────────────────────────────
setup_emqx_rules() {
  log_step "EMQX ルールエンジンを設定中"

  # EMQX Management API が応答するまで最大 60 秒待機
  local emqx_pass
  emqx_pass=$(grep ^EMQX_DASHBOARD_PASSWORD "$PROJECT_DIR/.env" | cut -d= -f2)
  local deadline=$(( $(date +%s) + 60 ))
  until curl -sf -u "admin:${emqx_pass}" \
        http://localhost:18083/api/v5/status > /dev/null 2>&1; do
    if (( $(date +%s) >= deadline )); then
      log_warn "EMQX API がタイムアウトしました。ルール設定をスキップします。"
      log_warn "後で手動実行: bash scripts/setup-emqx-rules.sh"
      return
    fi
    log_info "EMQX API 待機中..."
    sleep 5
  done

  cd "$PROJECT_DIR"
  bash scripts/setup-emqx-rules.sh
  log_ok "EMQX ルール設定完了（HTTP Action はダッシュボードで手動設定が必要です）"
}

# ─── シミュレータ依存関係 ─────────────────────────────────
setup_simulator() {
  log_step "Python SDK とシミュレータ依存関係をインストール中"
  cd "$PROJECT_DIR"
  $PYTHON -m pip install -q -e sdk/python
  $PYTHON -m pip install -q -r simulator/requirements.txt
  log_ok "SDK・シミュレータのインストール完了"
}

# ─── 完了メッセージ ───────────────────────────────────────
print_summary() {
  # 表示用に .env から再取得
  local emqx_pass admin_email
  emqx_pass=$(grep ^EMQX_DASHBOARD_PASSWORD "$PROJECT_DIR/.env" | cut -d= -f2)
  admin_email=$(grep ^PLATFORM_ADMIN_EMAIL "$PROJECT_DIR/.env" | cut -d= -f2-)

  echo ""
  echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${GREEN}║   IoT Platform セットアップ完了               ║${NC}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${BOLD}Admin UI${NC}          https://${PLATFORM_DOMAIN}/admin/"
  echo -e "                    ログイン: ${admin_email} (パスワードは .env の PLATFORM_ADMIN_PASSWORD)"
  echo -e "  ${BOLD}Grafana${NC}           https://${PLATFORM_DOMAIN}/grafana/ (Admin UI ログイン後)"
  echo -e "  ${BOLD}MailHog${NC}           http://localhost:8025"
  echo -e "  ${BOLD}MQTT Broker${NC}       mqtts://localhost:8883"
  echo ""
  echo -e "  ${YELLOW}▸ EMQX Dashboard はホストに公開されていません（内部ネットワークのみ）:${NC}"
  echo -e "    docker compose exec emqx emqx_ctl ... で操作するか、"
  echo -e "    一時的に 18083 を publish してアクセスしてください。"
  echo ""
  echo -e "  ${YELLOW}▸ EMQX 手動設定が必要です:${NC}"
  echo -e "    ダッシュボード (admin / ${emqx_pass}) → Rules"
  echo -e "    telemetry_ingest と status_ingest の Actions に追加:"
  echo -e "    HTTP Server → POST http://ingestion-service:8001/ingest"
  echo ""
  echo -e "  ${YELLOW}▸ ルート CA 証明書をブラウザにインポートしてください:${NC}"
  echo -e "    certs/ca/root_ca.crt"
  echo ""
  if $WITH_SIMULATOR; then
    echo -e "  ${YELLOW}▸ シミュレータ起動:${NC}"
    echo -e "    python3 simulator/simulator.py"
    echo -e "    ※ GUI 表示には X11 または仮想ディスプレイ (Xvfb) が必要です"
    echo ""
  fi
  echo -e "  ${BLUE}ヘルスチェック:${NC} bash scripts/health-check.sh"
  echo ""
}

# ─── メイン ───────────────────────────────────────────────
main() {
  echo -e "${BOLD}IoT Platform — Ubuntu クリーンインストール${NC}"
  echo -e "ドメイン: ${PLATFORM_DOMAIN}"
  echo ""

  check_prereqs
  install_docker
  resolve_docker_cmd
  install_python
  generate_env
  setup_hosts
  run_setup_certs
  start_services
  bootstrap_platform_admin
  setup_emqx_rules
  $WITH_SIMULATOR && setup_simulator || true
  print_summary
}

main
