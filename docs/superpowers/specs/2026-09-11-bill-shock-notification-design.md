# ビルショック通知機能 設計仕様書

**Goal:** テナントの当月請求金額（合計金額）が想定を超えて急増した場合に、PF管理者・テナント管理者へメール＋監査ログで通知する。

**背景:** [[project_tenant_billing_future]] — `docs/superpowers/specs/2026-09-10-billing-calculation-design.md` 2.5節で「ハードキャップは採用せず、想定利用量の閾値超過を検知して通知する」方針が確定済み。本仕様はその実装設計。

**参照:**
- `docs/superpowers/specs/2026-09-10-billing-calculation-design.md`（2.5節・3節・5節）
- `docs/superpowers/specs/2026-09-10-billing-calculation-phase2-design.md`（バッチ処理の既存実装）

**スコープ外（今回やらない。将来の別Planで検討）:**
- 個別デバイス単位の異常検知（暴走デバイスの特定）
- 閾値のデフォルト初期値の具体的な数値設定（PF管理者が画面から設定するまでは「未設定＝通知しない」扱いとし、安全側に倒す）
- EMQXのレート制限自体（既存の別対策として実装済み）

---

## 1. 判定基準・確定事項（ユーザーとのQ&Aで確定）

- **判定基準:** 当月draft請求書の`total_amount`（合計金額、円）が閾値を超えたら通知する。利用量（quantity）単位の個別項目しきい値は設けない。
- **閾値の形式:** 固定金額（円）。前月比パーセントのような相対値は採用しない。
- **通知頻度:** 月に1回のみ（当月最初に閾値を超えた時点でのみ通知する）。以降、同月内でバッチが再実行されて超過状態が続いても再通知しない。翌月になれば新しいdraft請求書に対して判定がリセットされる。
- **メール送信基盤:** core-apiに新規実装する（既存の`alert-service`とは独立。日次バッチが既にcore-api内で動いているため、そこから直接送信する）。

---

## 2. データモデル

既存テーブルへのカラム追加のみ。新規テーブルは作らない（閾値は時系列で履歴管理する必要がなく、単価マスタのような`effective_from`付き履歴構造は不要 — YAGNI）。

### 2.1 `billing_settings`（既存のシングルトン行テーブル）にカラム追加

```sql
ALTER TABLE billing_settings
    ADD COLUMN IF NOT EXISTS default_bill_shock_threshold_amount INTEGER
```

- `NULL`＝デフォルトしきい値が未設定（PF管理者が画面から設定するまで、しきい値未設定のテナントには通知しない）。
- 円単位の整数。

### 2.2 `tenants`（既存テーブル）にカラム追加

```sql
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS bill_shock_threshold_amount INTEGER
```

- `NULL`＝デフォルト値を使う（テナント個別の上書きなし）。
- テナント開通時（`seed_tenant_default_prices`と同じ`POST /tenants`のタイミング）に、`billing_settings.default_bill_shock_threshold_amount`をコピーする**必要はない** — `NULL`のままにしておけば「有効な閾値を都度デフォルトから解決する」ロジック（2.4節）が自動的にデフォルトを反映するため、コピー不要でPF管理者がデフォルトを事後変更した場合も既存テナントに自動反映される（単価マスタとの意図的な違い：単価は「変更しても既存テナントの契約条件を変えない」ため個別コピーが必要だったが、しきい値は単なる警告ラインなので後からデフォルトを変えれば全テナントに効いてよい）。

### 2.3 `billing_invoices`（既存テーブル）にカラム追加

```sql
ALTER TABLE billing_invoices
    ADD COLUMN IF NOT EXISTS bill_shock_notified_at TIMESTAMPTZ
```

- 通知済みなら通知日時を記録。`NULL`＝まだ通知していない。
- 月が変わって新しいdraft請求書が作られれば自動的に`NULL`から始まるため、月次リセットは追加ロジック不要。

---

## 3. しきい値の解決ロジック（`app/services/billing.py`に追加）

```python
def get_default_bill_shock_threshold(db: Session) -> int | None: ...
def set_default_bill_shock_threshold(db: Session, amount: int | None) -> None: ...
def get_effective_bill_shock_threshold(db: Session, tenant: Tenant) -> int | None:
    """tenant.bill_shock_threshold_amount があればそれを、無ければ
    billing_settings.default_bill_shock_threshold_amount を返す。両方NULLならNone
    （しきい値未設定＝通知しない）。"""
def set_tenant_bill_shock_threshold(db: Session, tenant_id: str, amount: int | None) -> None:
    """PF管理者がテナント個別の上書きを設定・解除する（Noneで解除＝デフォルトに戻す）。"""
def validate_bill_shock_threshold(value_str: str) -> int | None:
    """空文字列はNone（未設定/解除）として許可。それ以外は0以上の整数でなければ
    InvalidUnitPriceErrorを投げる。"""
```

---

## 4. 通知ロジック（新規 `app/services/bill_shock.py`）

```python
def check_and_notify_bill_shock(db: Session, tenant: Tenant, invoice: BillingInvoice) -> None:
    """draft請求書を再集計した直後に呼ぶ。閾値超過かつ未通知なら通知する。"""
```

処理内容:
1. `get_effective_bill_shock_threshold(db, tenant)`が`None`なら何もしない。
2. `invoice.total_amount <= threshold`なら何もしない。
3. `invoice.bill_shock_notified_at is not None`（今月既に通知済み）なら何もしない。
4. 監査ログを記録する（`write_audit_log`、`actor_type="system"`、`actor_id`はゼロUUID `"00000000-0000-0000-0000-000000000000"`をシステム操作の番兵値として使う、`actor_email="system@platform"`、`resource_type="billing_invoice"`、`detail`に`target_year_month`・`total_amount`・`threshold_amount`を含める）。
5. メール送信（5節）。
6. `invoice.bill_shock_notified_at = datetime.now(timezone.utc)`をセットして`db.commit()`。

**呼び出し箇所:** `billing_batch.py`の`run_monthly_billing_batch()`内、当月draft請求書の再計算・`db.commit()`直後（`_replace_line_items`呼び出しの前後どちらでも良いが、`db.commit()`後を推奨——`invoice.total_amount`が確定した後に判定する）。**`_backfill_missing_months`・`_finalize_stale_drafts`が生成・確定する過去月分のfinalized請求書には呼ばない**（過去月について今通知しても無意味なため、当月draftの経路のみに限定する）。

---

## 5. メール送信基盤（新規 `app/services/mailer.py`）

`alert-service/app/notifier.py`と同型の`smtplib`実装。

```python
def send_bill_shock_email(
    to_emails: list[str], tenant_name: str, target_year_month: str,
    total_amount: int, threshold_amount: int,
) -> None:
    """to_emailsが空なら何もしない。送信失敗はログ出力のみ（例外を伝播させない —
    通知失敗で請求バッチ本体を止めないため、既存のrun_monthly_billing_batchの
    tenant単位try/exceptと同じ思想）。"""
```

### 5.1 設定（`core-api/app/config.py`に追加）

`alert-service/app/config.py`と同じフィールド名・デフォルト値で追加する（`.env`の`SMTP_*`変数を両サービスで共有できるようにする）:

```python
smtp_host: str = "localhost"
smtp_port: int = 587
smtp_user: str = ""
smtp_password: str = ""
smtp_from: str = "alerts@iot-platform.local"
```

### 5.2 `docker-compose.yml`のcore-apiサービスに環境変数を追加

```yaml
SMTP_HOST: "${SMTP_HOST:-localhost}"
SMTP_PORT: "${SMTP_PORT:-587}"
SMTP_USER: "${SMTP_USER:-}"
SMTP_PASSWORD: "${SMTP_PASSWORD:-}"
SMTP_FROM: "${SMTP_FROM:-alerts@iot-platform.local}"
```

（`alert-service`と全く同じ変数名・デフォルト値なので、既存`.env`のSMTP設定がそのまま両サービスに適用される。新しい`.env`変数は不要。）

### 5.3 宛先の解決

- **テナント管理者:** `SELECT email FROM "{schema}".users WHERE role = 'admin' AND is_active`（既存の`tenant_users.py`のスキーマ参照パターンと同じ）。
- **PF管理者:** `SELECT email FROM platform_users WHERE is_active`（全員に送る。現状PF管理者向けの通知先リスト設定機能は無いため、既存の運用体制を前提にした最小実装とする）。

---

## 6. API

### 6.1 PF管理者向け（`core-api/app/routers/platform.py`に追記）

```
GET /platform/billing/bill-shock-threshold   → { "default_threshold_amount": "50000" | null }
PUT /platform/billing/bill-shock-threshold   ← { "default_threshold_amount": "50000" | null }
```

- 既存の`/platform/billing/tax-rate`と同じ形（`_require_platform`必須、`write_audit_log`＋`db.commit()`）。
- 文字列型で受け渡す（既存の`unit_price`/`tax_rate`と同じ流儀）。空文字列/`null`は「未設定に戻す」。

### 6.2 PF管理者向け（既存`core-api/app/routers/billing.py`に追記、テナント個別上書き）

```
GET /tenants/{tenant_id}/billing/bill-shock-threshold   → { "threshold_amount": "50000" | null, "is_default": true|false }
PUT /tenants/{tenant_id}/billing/bill-shock-threshold   ← { "threshold_amount": "50000" | null }
```

- `is_default`: テナント個別上書きが無く、デフォルト値を使っているかどうかをUIに伝えるためのフラグ。
- 既存の`/tenants/{tenant_id}/billing/prices`と同じ認証・パターン。

---

## 7. UI

### 7.1 PF管理者向けデフォルト設定（`platform-ui/platform-settings.html`）

既存の「消費税率」カードと同じ形式で、「ビルショック通知しきい値（デフォルト）」カードを追加する。空欄＝未設定（通知しない）を許可し、その旨を注記する。

### 7.2 PF管理者向けテナント個別設定（`platform-ui/tenant.html`の単価設定タブ）

既存の単価テーブルの下に、しきい値の個別上書き入力欄を1つ追加する。プレースホルダに現在の実効値（デフォルト値、または既存の個別設定値）を表示し、空欄で保存すると「デフォルトに戻す」動作にする。

---

## 8. エラー処理・耐障害性

- メール送信失敗（SMTP接続不可等）は`print`でログ出力し例外を握る。日次バッチの他テナント処理・請求計算自体を止めない（既存の`run_monthly_billing_batch`のtenant単位try/exceptと同じ思想）。
- 監査ログ書き込み失敗時は`write_audit_log`のいつも通りの挙動に従う（`db.commit()`は呼び出し側=`check_and_notify_bill_shock`の責務）。

---

## 9. テスト方針

- `app/services/billing.py`の閾値解決ロジック: 単体テスト（デフォルトのみ／上書きあり／両方NULL）。
- `app/services/bill_shock.py`の`check_and_notify_bill_shock`: 閾値未設定でスキップ／未超過でスキップ／超過だが既通知でスキップ／初回超過で通知＆`bill_shock_notified_at`セット、の4パターンをモックで検証。
- `app/services/mailer.py`: `smtplib.SMTP`をモックして送信内容を検証、`to_emails`空でno-opになることを検証。
- API（PF管理者向けデフォルト・テナント個別）: 既存の`test_platform_billing_defaults.py`/`test_billing_api.py`と同じ形式で認証・バリデーション・404系を検証。
- `run_monthly_billing_batch`への統合: `check_and_notify_bill_shock`が呼ばれることをモックで検証（既存`test_billing_batch.py`のパターンに追加）。
