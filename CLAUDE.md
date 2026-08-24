# IoT Platform — Claude Code コンテキスト

## プロジェクト概要

マルチテナント型 IoT デバイス管理基盤のプロトタイプ。
リポジトリ: https://github.com/taz-szk/iotpf-proto

## 現在のデバッグ課題（macOS）

**症状:** `install-mac.sh` で構築した環境で `nginx` と `emqx` コンテナが `Restarting` になる。

### まず実行すること

```bash
# 全サービスの状態を確認
docker compose ps

# nginx と emqx のログを確認（最後の 50 行）
docker compose logs --tail=50 nginx
docker compose logs --tail=50 emqx

# 証明書ディレクトリの存在確認（nginx/emqx の最頻出原因）
ls -la certs/server/
ls -la certs/ca/

# emqx 設定ファイルの確認
cat emqx/emqx.conf
```

### nginx が落ちる典型的な原因

1. **証明書ファイルが存在しない** — `certs/server/server.crt` / `server.key` / `certs/ca/root_ca.crt` が無いと起動直後にクラッシュする
   - `install-mac.sh` は step-ca が起動してから証明書を取得する。step-ca が healthy になる前に nginx が起動しようとすると証明書が無い
   - 対処: `docker compose restart nginx` を step-ca が healthy になってから実行

2. **nginx.conf のシンタックスエラー**
   ```bash
   docker compose exec nginx nginx -t
   ```

3. **80/443 ポートが既に使われている（macOS は AirPlay Receiver が 5000/7000 を、Bonjour が 5353 を使うが 443 は空のはず）**
   ```bash
   sudo lsof -i :80
   sudo lsof -i :443
   ```

### emqx が落ちる典型的な原因

1. **証明書ファイルが存在しない** — `certs/server/server.crt` / `server.key` / `certs/ca/root_ca.crt` が volume mount されているが、ファイルが無い場合はコンテナが落ちる
   ```bash
   ls -la certs/server/ certs/ca/
   ```

2. **emqx.conf の Webhook 設定で `EMQX_WEBHOOK_SECRET` が空**
   ```bash
   grep EMQX_WEBHOOK_SECRET .env
   ```

3. **ポート 8883 が既に使われている**
   ```bash
   sudo lsof -i :8883
   ```

4. **emqx データディレクトリの権限問題（macOS + Docker Desktop）**
   ```bash
   docker compose logs emqx | head -30
   ```

### インストーラーが生成する証明書の流れ

`install-mac.sh` の手順:
1. `.env` を生成（シークレット自動生成）
2. `docker compose up -d` でコンテナ起動
3. step-ca が healthy になるまで待機（最大 120 秒）
4. step-ca から証明書を取得して `certs/` に保存
5. nginx / emqx を再起動

→ step-ca の起動が遅いと証明書取得ステップをスキップしてしまい、nginx/emqx が証明書なしで起動する可能性がある。

```bash
# インストーラーを途中から再実行する代わりに手動で証明書取得
docker compose exec step-ca step ca certificate localhost certs/server/server.crt certs/server/server.key \
  --ca-url=https://localhost:9000 \
  --root=/home/step/certs/root_ca.crt \
  --not-after=8760h

# CA 証明書のコピー
docker compose exec step-ca cat /home/step/certs/root_ca.crt > certs/ca/root_ca.crt

# 証明書取得後に再起動
docker compose restart nginx emqx
```

## アーキテクチャ

```
[外部] → nginx (80/443) → core-api (8000)
                        → grafana (3000)
[デバイス] → emqx (8883 mTLS) → ingestion-service (8001) → influxdb
                              → core-api (webhook)
```

### サービス一覧

| サービス | イメージ | 役割 |
|----------|----------|------|
| postgres | postgres:16-alpine | メタデータ DB（マルチテナント） |
| influxdb | influxdb:2-alpine | 時系列テレメトリ |
| emqx | emqx:5 | MQTT ブローカー（mTLS） |
| step-ca | smallstep/step-ca | 内部 CA（デバイス証明書発行） |
| minio | minio/minio | ファームウェアストレージ |
| grafana | grafana/grafana-oss | ダッシュボード（Auth Proxy SSO） |
| nginx | nginx:1.25-alpine | リバースプロキシ（TLS終端） |
| mailhog | mailhog/mailhog | SMTP テスト用 |
| core-api | ./core-api | メイン API（FastAPI） |
| ingestion-service | ./ingestion-service | MQTT テレメトリ取込 |
| alert-service | ./alert-service | アラート評価・通知 |

### ネットワーク

- `internal` — サービス間通信
- `external` — nginx / emqx / mailhog のみ
- `grafana-net` (172.30.0.0/24) — nginx↔grafana の Auth Proxy 専用

### volume mount で証明書を使うサービス（起動順序に注意）

```yaml
# nginx
- ./certs/server/server.crt:/etc/nginx/certs/server.crt:ro
- ./certs/server/server.key:/etc/nginx/certs/server.key:ro
- ./certs/ca/root_ca.crt:/etc/nginx/certs/root_ca.crt:ro

# emqx
- ./certs/ca/root_ca.crt:/opt/emqx/etc/certs/root_ca.crt:ro
- ./certs/server/server.crt:/opt/emqx/etc/certs/server.crt:ro
- ./certs/server/server.key:/opt/emqx/etc/certs/server.key:ro
```

## セキュリティ実装済み（前セッションで完了）

- **H-1** IP レートリミット — `core-api/app/services/rate_limiter.py`
- **H-2** リフレッシュトークン失効 — JTI ブロックリスト + `token_version`（`platform_users` テーブル）
- **H-5** EMQX Webhook HMAC 署名検証 — `EMQX_WEBHOOK_SECRET` 環境変数
- **H-6** minio_key UUID 形式バリデーション

## デバッグに使うコマンド集

```bash
# 全ログをリアルタイム監視
docker compose logs -f nginx emqx

# コンテナ状態詳細
docker inspect nginx --format='{{.State.Status}} {{.State.Error}}'
docker inspect emqx  --format='{{.State.Status}} {{.State.Error}}'

# 証明書の有効期限確認
openssl x509 -in certs/server/server.crt -noout -dates 2>/dev/null || echo "証明書なし"

# emqx 設定テスト（コンテナ内から）
docker compose exec emqx emqx check_config

# .env の必須変数確認
grep -E "EMQX_WEBHOOK_SECRET|JWT_SECRET|STEP_CA_PASSWORD" .env
```
