# テレメトリデータ保持ルール機能 設計仕様書

**Goal:** InfluxDBに蓄積されるテナントのテレメトリデータ（`telemetry`/`device_status`）が無尽蔵に増えてストレージを圧迫する問題に対し、保持期間（何日より古いデータを自動削除するか）をPF管理者のデフォルト値＋テナントごとの個別設定で制御できるようにする。

**背景:** [[project_tenant_billing_future]] — テナント課金機能の実装完了を受け、次の運用課題としてユーザーから提起された（2026-09-12）。

**参照:**
- `core-api/app/services/tenant.py`（`setup_tenant`、InfluxDB org作成の既存実装）
- `ingestion-service/app/influx_writer.py`（`_ensure_bucket`、telemetryバケットの遅延作成）
- `core-api/app/services/billing_batch.py`（既存の日次バッチワーカーパターン）
- `docs/superpowers/specs/2026-09-11-bill-shock-notification-design.md`（デフォルト+個別上書きの2層設定パターンの前例）

**スコープ外（今回やらない）:**
- PostgreSQL側のデータ（audit_logs以外のテーブル）の保持ルール（既存の`audit_log_retention_days`はグローバル固定値のまま、今回変更しない）
- テナントごとの保持期間に上限（延長の天井）を設けること — ユーザーの明示的な要望により、短縮・延長ともに自由

---

## 1. 決定事項（ユーザーとのQ&Aで確定）

- **削除の実行方式:** InfluxDBネイティブのバケット単位retention rule機能を使う。自前の削除バッチは書かない。バケット単位の設定のため、同じ`telemetry`バケットに入っている`telemetry`測定値と`device_status`測定値の両方が対象になる（分離不可、InfluxDBの仕様上の制約であり選択の余地はない）。
- **PF管理者デフォルト値変更の反映タイミング:** 日次バッチで定期同期する（即時反映はしない）。全アクティブテナントの実効保持日数を計算し、InfluxDB側の現在値と異なればPATCHする。反映まで最大24時間の遅延を許容する。
- **保持期間の下限:** 60日。当月＋前月分のデータ（当月課金計算・バックフィル・月末再集計が必要とする範囲）に安全マージンを持たせるため。上限は設けない。
- **テナント個別設定の権限:** テナント管理者（セルフサービス）＋PF管理者（代理設定）の両方が変更可能。
- **課金バッチとの実行順序:** 日次サイクル内で「課金バッチ→保持期間同期」の順に実行する。60日下限により通常運用では競合しないが、課金バッチが長期停止していた場合のバックフィルに少しでも猶予を持たせるための防御的措置。

### 既知のトレードオフ（コードで解決しない、仕様として明記する）

課金バッチが保持期間より長い期間停止していた場合、復旧時のバックフィル（`_backfill_missing_months`）が必要とする過去月のテレメトリが既に削除されている可能性がある。この場合、その月の利用量は0件として計算され、過少請求になる。これは`provisionable_devices`が「現在時点のスナップショットで過去を再現できない」という既存の割り切りと同種のトレードオフであり、運用上のルール（保持期間はバッチの想定最大停止期間より十分長く設定する）でカバーする。

---

## 2. データモデル

### 2.1 `billing_settings`（既存のシングルトン設定テーブル）にカラム追加

```sql
ALTER TABLE billing_settings
    ADD COLUMN IF NOT EXISTS default_retention_days INTEGER NOT NULL DEFAULT 365
```

- 命名は`billing_settings`だが、税率・ビルショック通知しきい値のデフォルト値も既に同居しているPF共通設定テーブルとして扱う（新規テーブルは作らない、YAGNI）。
- `NOT NULL DEFAULT 365`：保持期間は「未設定」を許可しない（ビルショックしきい値と違い、無効化＝無期限保持を許すとストレージ課題の解決にならないため、常に具体的な日数を持つ）。

### 2.2 `tenants`（既存テーブル）にカラム追加

```sql
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS data_retention_days INTEGER
```

- `NULL`＝個別上書きなし、デフォルト値を使う。

---

## 3. サービス層（`app/services/billing.py`に追加、既存の`get_effective_bill_shock_threshold`と対になる関数群）

```python
def get_default_retention_days(db: Session) -> int: ...
def set_default_retention_days(db: Session, days: int) -> None: ...
def get_effective_retention_days(db: Session, tenant: Tenant) -> int:
    """tenant.data_retention_days があればそれを、無ければ
    billing_settings.default_retention_days を返す。billing_settingsは常に
    365デフォルトを持つ行がマイグレーションで存在するはずだが、既存のget_tax_rate等と
    同じ防御的パターンとして、万一行が無ければ365をフォールバックで返す。"""
def validate_retention_days(value_str: str) -> int:
    """60以上の整数でなければInvalidUnitPriceErrorを投げる。上限なし。
    空文字列は許可しない（保持期間の無効化はできない）。"""
```

---

## 4. InfluxDBへの反映

### 4.1 テナント開通時の先行作成（`app/services/tenant.py`の`setup_tenant`に追加）

現状、`telemetry`バケットは`ingestion-service`が初回テレメトリ受信時に無期限保持で遅延作成している（`_ensure_bucket`、retention rule未指定）。これを埋めるため、`setup_tenant()`内でテナント開通時点の実効デフォルト保持日数を使い、telemetryバケットを先に作成する:

```python
def create_influxdb_bucket(org_id: str, admin_token: str, retention_days: int) -> None:
    """telemetryバケットを指定の保持日数で作成する（既に存在する場合は何もしない）。"""
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/buckets",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={
            "orgID": org_id, "name": "telemetry",
            "retentionRules": [{"type": "expire", "everySeconds": retention_days * 86400}],
        },
        timeout=10.0,
    )
    if resp.status_code not in (201, 422):  # 422 = 既に存在（ingestion-serviceとの競合を許容）
        resp.raise_for_status()
```

既存の`tenant.py`の`httpx`直呼び出しスタイルを踏襲する（`ingestion-service`側の`influxdb_client`ライブラリはcore-apiに導入しない）。`ingestion-service`の`_ensure_bucket`（遅延作成のフォールバック）は変更不要——バケットが既に存在すればそちらの`create_bucket`呼び出しは何もしない。

### 4.2 日次同期ジョブ（新規 `app/services/data_retention.py`）

```python
def sync_tenant_retention(tenant_id: str, org_id: str, effective_days: int) -> None:
    """指定テナントのtelemetryバケットのretention ruleを実効値に同期する。
    バケットが存在しなければ何もしない（次回テレメトリ受信時に正しい値で作成される）。
    現在のretention_secondsが既に一致していればPATCHしない。"""

def run_daily_retention_sync() -> list[dict]:
    """全アクティブテナントについてsync_tenant_retentionを実行する。戻り値は各テナントの処理結果。"""

def start_data_retention_sync_worker() -> None:
    """毎日1回、run_daily_retention_syncを実行するバックグラウンドスレッドを起動する。
    既存のstart_billing_batch_worker/start_audit_purge_workerと同じdaemon thread+
    time.sleep(86400)パターン。main.pyでの起動順は課金バッチワーカーの後にする。

    **重要な限界:** これは「起動順を合わせる」だけの緩い担保であり、日々の実行完了順序を
    厳密に保証するものではない（各daemon threadは独立にsleep(86400)で回るため、
    処理時間のばらつきにより日を追うごとに順序がずれていく可能性がある）。
    正確な順序制御が必要になった場合は、将来的に1つの日次オーケストレーター関数から
    「課金バッチ→保持期間同期」を順に呼び出す形に統合することを検討する。
    現時点では60日下限（1節）が主たる安全策であり、この実行順序はその上に乗せる
    追加のsafety marginに過ぎない、という位置づけで良しとする。"""
```

**バケット検索・更新のAPI呼び出し:**
```
GET   /api/v2/buckets?orgID={org_id}&name=telemetry   → バケットIDと現在のretentionRulesを取得
PATCH /api/v2/buckets/{bucket_id}                      → retentionRulesを更新
```

---

## 5. API

### 5.1 PF管理者向けデフォルト設定（`core-api/app/routers/platform.py`に追記）

```
GET /platform/data-retention   → { "default_retention_days": "365" }
PUT /platform/data-retention   ← { "default_retention_days": "180" }
```

既存の`/platform/billing/tax-rate`と同じ形（`_require_platform`必須、`write_audit_log`＋`db.commit()`）。文字列型で受け渡す。

### 5.2 PF管理者向けテナント個別上書き（`core-api/app/routers/tenants.py`に追記）

```
GET /tenants/{tenant_id}/data-retention   → { "retention_days": "180", "is_default": false }
PUT /tenants/{tenant_id}/data-retention   ← { "retention_days": "180" | null }
```

billing関連ではない一般的なテナント設定のため、`billing.py`ではなく`tenants.py`ルーターに置く（`update_tenant`と同じ配置方針）。

### 5.3 テナント管理者向けセルフサービス（`core-api/app/routers/tenant_portal.py`に追記）

```
GET /tenant-portal/data-retention   → { "retention_days": "180", "is_default": false }
PUT /tenant-portal/data-retention   ← { "retention_days": "180" | null }
```

既存の`_require_admin`（role='admin'必須）を使う。

すべてのPUTエンドポイントは共通の`validate_retention_days`（60日下限）でバリデーションし、`InvalidUnitPriceError`を422にマッピングする。

---

## 6. UI

### 6.1 PF管理者向けデフォルト設定（`platform-ui/platform-settings.html`）

既存の「消費税率」「ビルショック通知しきい値（デフォルト）」カードと同じ形式で「データ保持期間（デフォルト）」カードを追加。60日未満を保存しようとすると、APIの422エラーをそのままエラー表示欄に出す（他の設定カードと同じ、送信後のサーバーエラー表示方式。入力中のライブバリデーションは行わない）。

### 6.2 PF管理者向けテナント個別上書き（`platform-ui/tenant.html`）

単価設定タブに、ビルショックしきい値の個別上書き入力欄と同じ形（実効値は別表示、編集欄は上書き値のみ）で追加。

### 6.3 テナント管理者向けセルフサービス（`admin-ui/tenant-portal.html`）

テナント管理者がrole='admin'の場合のみ表示される設定欄を追加（既存のユーザー管理タブ、または新規の設定セクション——実装計画で配置場所を確定する）。

---

## 7. テスト方針

- サービス層（`get_default_retention_days`/`set_default_retention_days`/`get_effective_retention_days`/`validate_retention_days`）: 単体テスト（デフォルトのみ／上書きあり／60日未満拒否／60日ちょうど許可）。
- `create_influxdb_bucket`: httpxをモックしてリクエストボディ（retentionRulesの秒数換算）を検証。
- `sync_tenant_retention`/`run_daily_retention_sync`: バケット未存在でスキップ／現在値と一致でPATCHしない／不一致でPATCHする、の3パターンをモックで検証。
- API（PF管理者向けデフォルト・テナント個別・テナント管理者セルフサービス）: 既存の`test_platform_billing_defaults.py`/`test_billing_api.py`/`test_tenant_portal_*.py`と同じ形式で認証・バリデーション・404系を検証。
