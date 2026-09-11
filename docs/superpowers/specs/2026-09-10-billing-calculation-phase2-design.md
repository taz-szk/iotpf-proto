# 料金積算機能 Phase 2 設計仕様書

**Goal:** 月次の実利用量をInfluxDB/PostgreSQLから集計し、実際の請求書（draft→finalized）を自動生成する。テナント管理者が自テナントの請求書（当月推定額＋過去の確定済み請求書）を確認できるようにする。

**前提:** Phase 1（単価マスタCRUD・積算エンジン・PF管理者向け単価設定UI）は完了・`origin/main`にマージ済み。本Phase 2はその続き。

**参照:**
- `docs/superpowers/specs/2026-09-10-billing-calculation-design.md`（全体仕様書）
- `docs/superpowers/plans/2026-09-10-billing-calculation-phase1.md`の「Phase 2への申し送り」

**スコープ外（今回やらない。将来の別Planで検討）:**
- ビルショック閾値の通知実装（閾値設定・通知ロジック自体）
- 確定済み(finalized)請求書の手動再集計「修正(corrected)」機能
- PF管理者向けの請求書確認UI画面（APIのみ用意し、画面は作らない）
- 消費税率のマスタ化（Phase 1同様ハードコード10%を継続）
- 個別デバイス単位の異常検知

---

## 1. 利用量集計サービス

新規ファイル: `core-api/app/services/billing_usage.py`

```python
def aggregate_monthly_usage(db: Session, tenant_id: str, schema: str, year: int, month: int) -> dict[str, int]:
    """対象年月の利用量を集計し、calculate_invoice()に渡せる usage dict を返す。"""
```

各`item_key`の算出方法:

| item_key | 算出方法 |
|---|---|
| `base_fee` | 常に`1`（固定量。単価そのものが月額基本料金） |
| `data_points` | InfluxDBの月範囲版クエリ。既存`app/routers/stats.py`の`_count_influxdb_points`は「常に現在の月」専用なので、`start`/`stop`を年月から計算する版を新設する（`_parse_influx_csv_scalar`はそのまま再利用） |
| `device_count` | 当月にテレメトリを送信したユニークdevice_name数。InfluxDBの`telemetry`measurementから`range(start: 月初, stop: 翌月初)`で`distinct(column: "device_name")`し件数を数える新規Fluxクエリ |
| `provisionable_devices` | `app.routers.stats._calc_provisionable_devices(db, tenant_id, schema)`をそのまま呼ぶ。これは「現在時点」のスナップショットであり過去の年月を再現できない値だが、仕様書2.2節の決定に従い、**draft更新時（＝当月分の集計時のみ）に呼ぶ**。finalized後は二度と呼ばれないため問題ない |
| `alert_events` | 当月のアラート発報件数。既存`stats.py`のカレンダー月集計パターン（`date_trunc('month', NOW())`版）を年月指定に一般化したSQL |

**テスト方針:** 各集計項目のFlux/SQLクエリ生成部分と、`aggregate_monthly_usage`全体を返り値の辞書構造でテストする。InfluxDB/DB呼び出しは`unittest.mock.patch`でモックする（既存`test_stats.py`と同じパターン）。

---

## 2. 請求書生成バッチ

新規ファイル: `core-api/app/services/billing_batch.py`

```python
def run_monthly_billing_batch() -> list[dict]:
    """全アクティブテナントについて、draft請求書のfinalized確定と当月draftの再集計を行う。
    戻り値は各テナントの処理結果リスト（ログ・テスト用）。"""
```

処理内容（テナントごと、既存の`with SessionLocal() as db:`パターンに従う）:

1. 今日の日付（JST）から対象年月`(target_year, target_month)`を決定する
2. そのテナントの`billing_invoices`のうち`status='draft'`の行を検索する。その`target_year_month`が現在の対象月と異なる（＝月が変わった）場合、`status='finalized'`, `finalized_at=now()`に更新する（**これが唯一のfinalized遷移経路**）
3. `aggregate_monthly_usage()`で対象年月の利用量を集計し、`get_effective_unit_prices()`で単価を取得し、`calculate_invoice()`で明細・税額・合計を計算する
4. 対象年月の`billing_invoices`行を`status='draft'`でupsert（`(tenant_id, target_year_month)`に既存行があれば`subtotal`/`tax_amount`/`total_amount`を更新、なければ新規作成）
5. その請求書の`billing_line_items`を全削除→計算結果で再挿入（既存のダッシュボードパネル設定PUTと同じ「全削除→再挿入」パターン）

**べき等性:** 同日中に複数回実行されても安全（3〜5は常に「今の集計結果で置き換える」だけで、finalizedになった行は2のガードで二度と触れない）。

**スケジューラ:** `core-api/app/main.py`の起動処理（`@app.on_event("startup")`、既存の他の初期化処理と同じ場所）で`APScheduler`の`BackgroundScheduler`を生成し、`run_monthly_billing_batch`を`cron`トリガーで登録する。`docker-compose.yml`のcore-apiサービスに`TZ`環境変数の指定がないためコンテナはUTC稼働。JST 0:10相当を狙って**UTC 15:10（前日）**にcronトリガーを設定する。`BlockingScheduler`（alert-serviceが使用）とは異なり、`BackgroundScheduler`はFastAPIのイベントループをブロックしない。

**テスト方針:** `run_monthly_billing_batch`はDBアクセスをモックし、①月が変わった場合にfinalizedへ更新される、②今月分がdraftとしてupsertされる、③既存のfinalized行は再計算されない、の3パターンを単体テストする。

---

## 3. API

### 3.1 テナントポータル（`core-api/app/routers/tenant_portal.py`に追記）

```
GET /tenant-portal/billing/invoices
GET /tenant-portal/billing/invoices/{target_year_month}
```

- 認証: 既存の`_require_tenant`（viewer/operator/admin全員に許可。統計タブと同じ扱い）
- 一覧レスポンス: `[{"target_year_month": "2026-09", "status": "draft", "subtotal": 12000, "tax_amount": 1200, "total_amount": 13200}, ...]`（新しい月順）
- 詳細レスポンス: 上記に`"line_items": [{"item_key": "data_points", "quantity": 12345, "unit_price": "0.10", "amount": 1234}, ...]`を加えたもの
- 対象月が存在しない（まだバッチが1度も走っていない等）場合は404

### 3.2 PF管理者（既存`core-api/app/routers/billing.py`に追記）

```
GET /tenants/{tenant_id}/billing/invoices
```

テナントポータル一覧と同じ形式。サポート目的の確認用途で、画面は作らない（curlやAPIクライアントから利用する想定）。

---

## 4. テナント管理者UI（`admin-ui/tenant-portal.html`）

- タブ一覧の末尾（`統計`タブの直後、既存の「共有タブ群の一番下」という配置ルールを継承）に`{ id: 'billing', label: '請求', icon: '💰' }`を追加
- 一覧: 対象月・ステータス（`draft`は「集計中（今月）」、`finalized`は「確定」と表示）・合計金額をテーブル表示
- 行クリックで明細（内訳・数量・単価・小計・消費税・合計）を展開表示（既存の「統計」タブのカード形式、または「テナント一覧」の展開行パターンを踏襲）
- draft行には「※このバッチは毎日更新されるため、月末までの推定額です」という注記を表示する

---

## 5. データモデルへの変更

なし。Phase 1で作成済みの`billing_invoices`/`billing_line_items`テーブルをそのまま使う。

---

## 6. 未確定・次回検討事項

| 項目 | 内容 |
|---|---|
| ビルショック通知 | 閾値設定UI・通知ロジック（別Plan） |
| 修正(corrected)機能 | finalized請求書の手動再集計・差分記録（別Plan） |

**解決済み（2026-09-11追加実装）:**
- PF管理者向け請求書確認UI → `platform-ui/tenant.html`の単価設定タブに一覧+明細ドリルダウンを追加。`GET /tenants/{tenant_id}/billing/invoices/{target_year_month}`を新設。
- 消費税率のマスタ化 → `billing_settings`テーブル（シングルトン行）+ `GET/PUT /platform/billing/tax-rate` + `platform-settings.html`のUIで実装。ハードコードのDEFAULT_TAX_RATEはフォールバックとして残す。
- バックフィル → `_backfill_missing_months()`を実装。既存の請求書と現在の対象月の間に抜けている月があればfinalizedとして遡って生成する。provisionable_devicesは直近の既存請求書の値を引き継ぐ（過去のスナップショットは再現できないため）。
