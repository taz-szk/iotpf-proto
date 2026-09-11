# ビルショック通知機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナントの当月請求金額（合計金額）が想定を超えて急増した場合に、PF管理者・テナント管理者へメール＋監査ログで通知する。

**Architecture:** 既存の`billing_settings`/`tenants`/`billing_invoices`テーブルにカラムを追加するだけで新規テーブルは作らない。日次バッチ（`billing_batch.py`の`run_monthly_billing_batch`）が当月draft請求書を再集計した直後に、新しい判定モジュール`app/services/bill_shock.py`が閾値超過を検知し、監査ログ記録＋新規メール送信基盤（`app/services/mailer.py`）でPF管理者・テナント管理者に通知する。通知は月1回のみ（`billing_invoices.bill_shock_notified_at`でdedup）。

**Tech Stack:** FastAPI, SQLAlchemy ORM（一部生SQL）, Pydantic, `smtplib`（標準ライブラリ）, Alpine.js, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-09-11-bill-shock-notification-design.md`

## Global Constraints

- 判定基準は当月draft請求書の`total_amount`（合計金額、円）のみ。利用量（quantity）単位のしきい値は設けない。
- 閾値は固定金額（円、整数）。前月比パーセントのような相対値は使わない。
- 通知は月1回のみ（当月最初に閾値を超えた時点のみ）。
- しきい値が未設定（デフォルトもテナント個別も両方NULL）の場合は通知しない（安全側のデフォルト）。
- メール送信はcore-apiに新規実装する（`alert-service`とは独立、既存の`.env`のSMTP_*変数をそのまま共有する）。
- メール送信失敗は`print`でログ出力し例外を握る。バッチ本体を止めない。
- `_backfill_missing_months`・`_finalize_stale_drafts`が生成・確定する過去月分の請求書には通知判定を呼ばない（当月draftの経路のみ）。

---

### Task 1: スキーマ変更（テーブルカラム追加）

**Files:**
- Modify: `core-api/app/database.py`（末尾、`migrate_create_billing_settings`の後に追記）
- Modify: `core-api/app/models/billing.py`（`BillingSettings`, `BillingInvoice`にカラム追加）
- Modify: `core-api/app/models/public.py`（`Tenant`にカラム追加）
- Modify: `core-api/app/main.py`（マイグレーション関数のimport・起動時実行リストに追加）
- Test: `core-api/tests/test_db.py`（新規マイグレーション関数のテスト追加）
- Test: `core-api/tests/test_billing_settings_model.py`（カラム追加分のアサーション更新）

**Interfaces:**
- Produces: `BillingSettings.default_bill_shock_threshold_amount`（`int | None`）, `Tenant.bill_shock_threshold_amount`（`int | None`）, `BillingInvoice.bill_shock_notified_at`（`datetime | None`）。以降のタスクはこれらのカラムを直接参照する。

- [ ] **Step 1: 失敗するテストを書く（マイグレーション関数）**

`core-api/tests/test_db.py`の末尾に追記:

```python
def test_migrate_add_bill_shock_threshold_columns_executes():
    from app.database import migrate_add_bill_shock_threshold_columns
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        migrate_add_bill_shock_threshold_columns()
    sql_calls = _sql_text(mock_conn.execute.call_args_list)
    assert "billing_settings" in sql_calls and "default_bill_shock_threshold_amount" in sql_calls
    assert "tenants" in sql_calls and "bill_shock_threshold_amount" in sql_calls
    assert "billing_invoices" in sql_calls and "bill_shock_notified_at" in sql_calls
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py::test_migrate_add_bill_shock_threshold_columns_executes -v`
Expected: FAIL（`ImportError: cannot import name 'migrate_add_bill_shock_threshold_columns'`）

- [ ] **Step 3: `database.py`にマイグレーション関数を追加する**

`core-api/app/database.py`の末尾（`migrate_create_billing_settings`関数の後）に追記:

```python


def migrate_add_bill_shock_threshold_columns() -> None:
    """ビルショック通知のしきい値・通知済みフラグ用カラムを追加する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE billing_settings
            ADD COLUMN IF NOT EXISTS default_bill_shock_threshold_amount INTEGER
        """))
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS bill_shock_threshold_amount INTEGER
        """))
        conn.execute(text("""
            ALTER TABLE billing_invoices
            ADD COLUMN IF NOT EXISTS bill_shock_notified_at TIMESTAMPTZ
        """))
        conn.commit()
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py::test_migrate_add_bill_shock_threshold_columns_executes -v`
Expected: PASS

- [ ] **Step 5: モデルにカラムを追加する**

`core-api/app/models/billing.py`の`BillingSettings`クラスを以下に置き換え:

```python
class BillingSettings(Base):
    __tablename__ = "billing_settings"
    id = Column(Integer, primary_key=True, default=1)
    tax_rate = Column(Numeric(5, 4), nullable=False)
    default_bill_shock_threshold_amount = Column(Integer, nullable=True)
```

同ファイルの`BillingInvoice`クラスに1行追加（`finalized_at`の後）:

```python
class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    target_year_month = Column(String(7), nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    subtotal = Column(Integer, nullable=False, default=0)
    tax_amount = Column(Integer, nullable=False, default=0)
    total_amount = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    bill_shock_notified_at = Column(DateTime(timezone=True), nullable=True)
```

`core-api/app/models/public.py`の`Tenant`クラスに1行追加（`updated_at`の後）:

```python
class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(100), nullable=False, unique=True)
    influxdb_org_id = Column(String(255))
    influxdb_token = Column(String)
    grafana_org_id = Column(String(255))
    public_token = Column(String(255), nullable=True, unique=True)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    bill_shock_threshold_amount = Column(Integer, nullable=True)
```

- [ ] **Step 6: `test_billing_settings_model.py`を更新する**

`core-api/tests/test_billing_settings_model.py`の`assert columns == {"id", "tax_rate"}`を以下に変更:

```python
    assert columns == {"id", "tax_rate", "default_bill_shock_threshold_amount"}
```

- [ ] **Step 7: main.pyにマイグレーションを配線する**

`core-api/app/main.py`の以下の行:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings
```

を以下に置き換え:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns
```

同ファイルの以下の行:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings):
```

を以下に置き換え:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns):
```

- [ ] **Step 8: 全テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py tests/test_billing_settings_model.py tests/test_billing_models.py -v`
Expected: PASS（全件）

- [ ] **Step 9: コミット**

```bash
git add core-api/app/database.py core-api/app/models/billing.py core-api/app/models/public.py core-api/app/main.py core-api/tests/test_db.py core-api/tests/test_billing_settings_model.py
git commit -m "feat: ビルショック通知用のカラムを追加(billing_settings/tenants/billing_invoices)"
```

---

### Task 2: しきい値の解決・設定サービス関数

**Files:**
- Modify: `core-api/app/services/billing.py`（末尾に追記）
- Test: `core-api/tests/test_bill_shock_threshold.py`（新規）

**Interfaces:**
- Consumes: Task 1の`BillingSettings.default_bill_shock_threshold_amount`, `Tenant.bill_shock_threshold_amount`
- Produces:
  - `get_default_bill_shock_threshold(db: Session) -> int | None`
  - `set_default_bill_shock_threshold(db: Session, amount: int | None) -> None`
  - `get_effective_bill_shock_threshold(db: Session, tenant: Tenant) -> int | None`（Task 4・Task 5が使用）
  - `validate_bill_shock_threshold(value_str: str) -> int | None`（Task 5が使用。既存の`InvalidUnitPriceError`を再利用）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_bill_shock_threshold.py`を新規作成:

```python
from unittest.mock import MagicMock
import pytest

from app.services.billing import (
    InvalidUnitPriceError,
    get_default_bill_shock_threshold,
    get_effective_bill_shock_threshold,
    set_default_bill_shock_threshold,
    validate_bill_shock_threshold,
)


def test_get_default_bill_shock_threshold_returns_value():
    row = MagicMock(default_bill_shock_threshold_amount=50000)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_default_bill_shock_threshold(mock_db) == 50000


def test_get_default_bill_shock_threshold_none_when_no_row():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_default_bill_shock_threshold(mock_db) is None


def test_get_default_bill_shock_threshold_none_when_column_unset():
    row = MagicMock(default_bill_shock_threshold_amount=None)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_default_bill_shock_threshold(mock_db) is None


def test_set_default_bill_shock_threshold_updates_existing_row():
    row = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    set_default_bill_shock_threshold(mock_db, 60000)
    assert row.default_bill_shock_threshold_amount == 60000
    mock_db.commit.assert_called_once()


def test_set_default_bill_shock_threshold_creates_row_when_missing():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    set_default_bill_shock_threshold(mock_db, 60000)
    assert mock_db.add.called
    added = mock_db.add.call_args[0][0]
    assert added.id == 1
    assert added.default_bill_shock_threshold_amount == 60000
    mock_db.commit.assert_called_once()


def test_get_effective_bill_shock_threshold_uses_tenant_override():
    tenant = MagicMock(bill_shock_threshold_amount=12345)
    mock_db = MagicMock()
    assert get_effective_bill_shock_threshold(mock_db, tenant) == 12345
    mock_db.query.assert_not_called()


def test_get_effective_bill_shock_threshold_falls_back_to_default():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    default_row = MagicMock(default_bill_shock_threshold_amount=99999)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = default_row
    assert get_effective_bill_shock_threshold(mock_db, tenant) == 99999


def test_get_effective_bill_shock_threshold_none_when_both_unset():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_effective_bill_shock_threshold(mock_db, tenant) is None


def test_validate_bill_shock_threshold_accepts_valid_integer():
    assert validate_bill_shock_threshold("50000") == 50000


def test_validate_bill_shock_threshold_empty_string_means_unset():
    assert validate_bill_shock_threshold("") is None


def test_validate_bill_shock_threshold_rejects_negative():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("-1")


def test_validate_bill_shock_threshold_rejects_non_integer():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("not-a-number")


def test_validate_bill_shock_threshold_rejects_decimal():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("50000.5")
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_bill_shock_threshold.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: `billing.py`に実装を追加する**

`core-api/app/services/billing.py`の末尾に追記:

```python


def get_default_bill_shock_threshold(db: Session) -> int | None:
    """設定済みのデフォルトビルショック通知しきい値（円）を返す。未設定ならNone。"""
    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    if row is None:
        return None
    return row.default_bill_shock_threshold_amount


def set_default_bill_shock_threshold(db: Session, amount: int | None) -> None:
    """デフォルトのビルショック通知しきい値を更新する（シングルトン行、常にid=1）。"""
    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    if row is None:
        db.add(BillingSettings(id=1, tax_rate=DEFAULT_TAX_RATE, default_bill_shock_threshold_amount=amount))
    else:
        row.default_bill_shock_threshold_amount = amount
    db.commit()


def get_effective_bill_shock_threshold(db: Session, tenant) -> int | None:
    """テナント個別のしきい値上書きがあればそれを、無ければデフォルト値を返す。
    両方未設定ならNone（＝ビルショック通知しない）。"""
    if tenant.bill_shock_threshold_amount is not None:
        return tenant.bill_shock_threshold_amount
    return get_default_bill_shock_threshold(db)


def validate_bill_shock_threshold(value_str: str) -> int | None:
    """しきい値の文字列をintに変換する。空文字列は「未設定に戻す」を意味しNoneを返す。
    不正な場合はInvalidUnitPriceErrorを投げる。"""
    if value_str == "":
        return None
    try:
        amount = int(value_str)
    except ValueError:
        raise InvalidUnitPriceError("threshold_amount must be an integer")
    if str(amount) != value_str.lstrip("+"):
        raise InvalidUnitPriceError("threshold_amount must be an integer")
    if amount < 0:
        raise InvalidUnitPriceError("threshold_amount must not be negative")
    return amount
```

（`validate_bill_shock_threshold`の`str(amount) != value_str.lstrip("+")`チェックは、`int("50000.5")`のような小数文字列を`int()`が例外なく切り捨てて受理してしまうことがない — 実際には`int("50000.5")`はValueErrorになるため通常は不要に見えるが、`int()`は`"  50000  "`のような前後空白付き文字列や`"+50000"`も受理してしまうため、往復文字列比較で「見た目通りの整数」だけを許可する。）

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_bill_shock_threshold.py -v`
Expected: PASS（全13件）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/billing.py core-api/tests/test_bill_shock_threshold.py
git commit -m "feat: ビルショック通知しきい値の解決・設定ロジックを追加"
```

---

### Task 3: メール送信基盤

**Files:**
- Create: `core-api/app/services/mailer.py`
- Modify: `core-api/app/config.py`（SMTP設定を追加）
- Modify: `docker-compose.yml`（core-apiサービスにSMTP環境変数を追加）
- Test: `core-api/tests/test_mailer.py`（新規）

**Interfaces:**
- Produces: `send_bill_shock_email(to_emails: list[str], tenant_name: str, target_year_month: str, total_amount: int, threshold_amount: int) -> None`（Task 4が使用）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_mailer.py`を新規作成:

```python
from unittest.mock import patch, MagicMock

from app.services.mailer import send_bill_shock_email


def test_send_bill_shock_email_sends_when_recipients_present():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = lambda s: mock_server
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_bill_shock_email(
            to_emails=["admin@example.com"], tenant_name="Acme Corp",
            target_year_month="2026-09", total_amount=120000, threshold_amount=100000,
        )
    mock_smtp.assert_called_once()
    mock_server.sendmail.assert_called_once()
    args = mock_server.sendmail.call_args[0]
    assert "admin@example.com" in args[1]
    assert "2026-09" in args[2]
    assert "120000" in args[2]
    assert "100000" in args[2]


def test_send_bill_shock_email_no_recipients_is_noop():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        send_bill_shock_email(
            to_emails=[], tenant_name="Acme Corp",
            target_year_month="2026-09", total_amount=120000, threshold_amount=100000,
        )
    mock_smtp.assert_not_called()


def test_send_bill_shock_email_swallows_smtp_errors():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=OSError("connection refused")):
        send_bill_shock_email(
            to_emails=["admin@example.com"], tenant_name="Acme Corp",
            target_year_month="2026-09", total_amount=120000, threshold_amount=100000,
        )
    # 例外が外に伝播しなければ成功
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_mailer.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.mailer'`）

- [ ] **Step 3: `config.py`にSMTP設定を追加する**

`core-api/app/config.py`の`audit_log_retention_days: int = 365`の行の直後に追記:

```python
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@iot-platform.local"
```

- [ ] **Step 4: `mailer.py`を新規作成する**

`core-api/app/services/mailer.py`:

```python
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings


def send_bill_shock_email(
    to_emails: list[str], tenant_name: str, target_year_month: str,
    total_amount: int, threshold_amount: int,
) -> None:
    """ビルショック通知メールを送信する。to_emailsが空なら何もしない。
    送信失敗は例外を伝播させずログ出力のみ（呼び出し元の請求バッチを止めないため）。"""
    if not to_emails:
        return

    tenant_name_clean = str(tenant_name).replace("\r", "").replace("\n", "")
    subject = f"[Bill Shock Alert] {tenant_name_clean} / {target_year_month}"
    body = (
        f"Bill shock threshold exceeded.\n"
        f"Tenant: {tenant_name_clean}\n"
        f"Target month: {target_year_month}\n"
        f"Current total amount: {total_amount}\n"
        f"Threshold: {threshold_amount}\n"
    )

    msg = MIMEMultipart()
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, to_emails, msg.as_string())
    except Exception as e:
        print(f"Bill shock email send failed: {e}")
```

- [ ] **Step 5: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_mailer.py -v`
Expected: PASS（全3件）

- [ ] **Step 6: `docker-compose.yml`にSMTP環境変数を追加する**

`docker-compose.yml`のcore-apiサービスの`environment:`ブロック内、`GRAFANA_ADMIN_PASSWORD: "${GRAFANA_ADMIN_PASSWORD}"`の行の直後に追記:

```yaml
      SMTP_HOST: "${SMTP_HOST:-localhost}"
      SMTP_PORT: "${SMTP_PORT:-587}"
      SMTP_USER: "${SMTP_USER:-}"
      SMTP_PASSWORD: "${SMTP_PASSWORD:-}"
      SMTP_FROM: "${SMTP_FROM:-alerts@iot-platform.local}"
```

- [ ] **Step 7: コミット**

```bash
git add core-api/app/services/mailer.py core-api/app/config.py core-api/tests/test_mailer.py docker-compose.yml
git commit -m "feat: core-apiにビルショック通知用のメール送信基盤を追加"
```

---

### Task 4: 通知判定ロジック・バッチ統合

**Files:**
- Create: `core-api/app/services/bill_shock.py`
- Modify: `core-api/app/services/billing_batch.py`
- Test: `core-api/tests/test_bill_shock.py`（新規）
- Test: `core-api/tests/test_billing_batch.py`（統合呼び出しのテスト追加）

**Interfaces:**
- Consumes: `get_effective_bill_shock_threshold`（Task 2）, `send_bill_shock_email`（Task 3）, `write_audit_log`（既存`app.services.audit`）
- Produces: `check_and_notify_bill_shock(db, tenant, schema: str, invoice: BillingInvoice) -> None`（`run_monthly_billing_batch`が呼ぶ）

- [ ] **Step 1: 失敗するテストを書く（`bill_shock.py`本体）**

`core-api/tests/test_bill_shock.py`を新規作成:

```python
from unittest.mock import MagicMock, patch

from app.services.bill_shock import check_and_notify_bill_shock


def _tenant(threshold=None, name="Acme Corp", tenant_id="tenant-1"):
    t = MagicMock()
    t.id = tenant_id
    t.name = name
    t.bill_shock_threshold_amount = threshold
    return t


def test_skips_when_no_effective_threshold():
    tenant = _tenant(threshold=None)
    invoice = MagicMock(total_amount=999999, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=None), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_skips_when_under_threshold():
    tenant = _tenant(threshold=100000)
    invoice = MagicMock(total_amount=50000, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_skips_when_already_notified_this_month():
    tenant = _tenant(threshold=100000)
    invoice = MagicMock(total_amount=150000, bill_shock_notified_at="2026-09-05T00:00:00Z")
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_notifies_on_first_excess_and_sets_notified_at():
    tenant = _tenant(threshold=100000, name="Acme Corp", tenant_id="tenant-1")
    invoice = MagicMock(target_year_month="2026-09", total_amount=150000, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock._get_tenant_admin_emails", return_value=["tenant-admin@example.com"]), \
         patch("app.services.bill_shock._get_platform_admin_emails", return_value=["pf-admin@example.com"]), \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)

    mock_audit.assert_called_once()
    audit_kwargs = mock_audit.call_args
    assert audit_kwargs.args[1] == "system"
    assert audit_kwargs.kwargs["tenant_id"] == "tenant-1"

    mock_mail.assert_called_once_with(
        to_emails=["tenant-admin@example.com", "pf-admin@example.com"],
        tenant_name="Acme Corp", target_year_month="2026-09",
        total_amount=150000, threshold_amount=100000,
    )
    assert invoice.bill_shock_notified_at is not None
    mock_db.commit.assert_called_once()


def test_get_tenant_admin_emails_queries_tenant_schema():
    from app.services.bill_shock import _get_tenant_admin_emails
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    row = MagicMock(email="admin@tenant.example.com")
    mock_conn.execute.return_value.fetchall.return_value = [row]
    with patch("app.services.bill_shock.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        result = _get_tenant_admin_emails("tenant_x")
    assert result == ["admin@tenant.example.com"]
    sql = str(mock_conn.execute.call_args[0][0])
    assert "tenant_x" in sql and "role" in sql and "is_active" in sql


def test_get_platform_admin_emails_queries_active_platform_users():
    from app.services.bill_shock import _get_platform_admin_emails
    row = MagicMock(email="pf-admin@example.com")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [row]
    result = _get_platform_admin_emails(mock_db)
    assert result == ["pf-admin@example.com"]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_bill_shock.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: `bill_shock.py`を新規作成する**

`core-api/app/services/bill_shock.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import engine
from app.models.billing import BillingInvoice
from app.models.public import PlatformUser
from app.services.audit import write_audit_log
from app.services.billing import get_effective_bill_shock_threshold
from app.services.mailer import send_bill_shock_email

SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000000"
SYSTEM_ACTOR_EMAIL = "system@platform"


def _get_tenant_admin_emails(schema: str) -> list[str]:
    """そのテナントのrole='admin'かつ有効なユーザーのメールアドレス一覧を返す。"""
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT email FROM "{schema}".users WHERE role = \'admin\' AND is_active')
        ).fetchall()
    return [r.email for r in rows]


def _get_platform_admin_emails(db) -> list[str]:
    """有効な全PF管理者のメールアドレス一覧を返す。"""
    rows = db.query(PlatformUser).filter(PlatformUser.is_active == True).all()  # noqa: E712
    return [r.email for r in rows]


def check_and_notify_bill_shock(db, tenant, schema: str, invoice: BillingInvoice) -> None:
    """当月draft請求書の合計金額がしきい値を超過していれば、監査ログ記録＋メール送信で
    通知する。しきい値未設定・未超過・今月既通知のいずれかならno-op。"""
    threshold = get_effective_bill_shock_threshold(db, tenant)
    if threshold is None:
        return
    if invoice.total_amount <= threshold:
        return
    if invoice.bill_shock_notified_at is not None:
        return

    write_audit_log(
        db, "system", SYSTEM_ACTOR_ID, SYSTEM_ACTOR_EMAIL, "bill_shock_threshold_exceeded",
        tenant_id=str(tenant.id), resource_type="billing_invoice",
        detail={
            "target_year_month": invoice.target_year_month,
            "total_amount": invoice.total_amount,
            "threshold_amount": threshold,
        },
    )

    to_emails = _get_tenant_admin_emails(schema) + _get_platform_admin_emails(db)
    send_bill_shock_email(
        to_emails=to_emails, tenant_name=tenant.name,
        target_year_month=invoice.target_year_month,
        total_amount=invoice.total_amount, threshold_amount=threshold,
    )

    invoice.bill_shock_notified_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_bill_shock.py -v`
Expected: PASS（全6件）

- [ ] **Step 5: `run_monthly_billing_batch`に統合する（失敗するテストを先に書く）**

`core-api/tests/test_billing_batch.py`の`test_run_monthly_billing_batch_happy_path`を以下に置き換え（`patch("app.services.billing_batch.check_and_notify_bill_shock")`を追加し、呼び出しを検証するアサーションを追加）:

```python
def test_run_monthly_billing_batch_happy_path():
    tenant = MagicMock(id="tenant-1", status="active", influxdb_org_id="org-1", influxdb_token="tok")
    mock_list_db = MagicMock()
    mock_list_db.__enter__ = lambda s: mock_list_db
    mock_list_db.__exit__ = MagicMock(return_value=False)
    mock_list_db.query.return_value.filter.return_value.all.return_value = [tenant]

    mock_tenant_db = MagicMock()
    mock_tenant_db.__enter__ = lambda s: mock_tenant_db
    mock_tenant_db.__exit__ = MagicMock(return_value=False)
    draft_invoice = MagicMock(status="draft", id="invoice-1")
    # 1st .first(): tenant再取得, 2nd .first(): draft請求書取得
    mock_tenant_db.query.return_value.filter.return_value.first.side_effect = [tenant, draft_invoice]
    mock_tenant_db.query.return_value.filter.return_value.all.return_value = []  # 失効対象のdraftなし

    with patch("app.services.billing_batch.SessionLocal", side_effect=[mock_list_db, mock_tenant_db]), \
         patch("app.services.billing_batch.aggregate_monthly_usage", return_value={"base_fee": 1}), \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={"base_fee": Decimal("5000")}), \
         patch("app.services.billing_batch.get_tax_rate", return_value=Decimal("0.08")), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [{"item_key": "base_fee", "quantity": 1, "unit_price": Decimal("5000"), "amount": 5000}],
             "subtotal": 5000, "tax_amount": 500, "total_amount": 5500,
         }) as mock_calc, \
         patch("app.services.billing_batch.check_and_notify_bill_shock") as mock_notify:
        results = run_monthly_billing_batch()

    assert results == [{"tenant_id": "tenant-1", "status": "ok", "total_amount": 5500}]
    assert draft_invoice.subtotal == 5000
    assert draft_invoice.total_amount == 5500
    assert mock_calc.call_args[0][2] == Decimal("0.08")
    mock_notify.assert_called_once_with(mock_tenant_db, tenant, "tenant_tenant_1", draft_invoice)
```

`test_run_monthly_billing_batch_skips_already_finalized_invoice`を以下に置き換え:

```python
def test_run_monthly_billing_batch_skips_already_finalized_invoice():
    tenant = MagicMock(id="tenant-1", status="active", influxdb_org_id="org-1", influxdb_token="tok")
    mock_list_db = MagicMock()
    mock_list_db.__enter__ = lambda s: mock_list_db
    mock_list_db.__exit__ = MagicMock(return_value=False)
    mock_list_db.query.return_value.filter.return_value.all.return_value = [tenant]

    mock_tenant_db = MagicMock()
    mock_tenant_db.__enter__ = lambda s: mock_tenant_db
    mock_tenant_db.__exit__ = MagicMock(return_value=False)
    already_finalized = MagicMock(status="finalized")
    # 1st .first(): tenant再取得, 2nd .first(): 既存請求書取得（finalized済み）
    mock_tenant_db.query.return_value.filter.return_value.first.side_effect = [tenant, already_finalized]
    mock_tenant_db.query.return_value.filter.return_value.all.return_value = []

    with patch("app.services.billing_batch.SessionLocal", side_effect=[mock_list_db, mock_tenant_db]), \
         patch("app.services.billing_batch.aggregate_monthly_usage", return_value={"base_fee": 1}) as mock_aggregate, \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={}), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [], "subtotal": 0, "tax_amount": 0, "total_amount": 0,
         }), \
         patch("app.services.billing_batch.check_and_notify_bill_shock") as mock_notify:
        results = run_monthly_billing_batch()

    assert results == [{"tenant_id": "tenant-1", "status": "skipped_not_draft"}]
    mock_notify.assert_not_called()
```

- [ ] **Step 6: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_billing_batch.py -v`
Expected: FAIL（`run_monthly_billing_batch`が`check_and_notify_bill_shock`を呼んでいない、または`side_effect`不足でエラー）

- [ ] **Step 7: `billing_batch.py`を修正する**

`core-api/app/services/billing_batch.py`の先頭のimport群に追記:

```python
from app.services.bill_shock import check_and_notify_bill_shock
```

`run_monthly_billing_batch`関数内の以下のブロック:

```python
    for tenant_id, schema, influxdb_org_id, influxdb_token in tenant_infos:
        try:
            with SessionLocal() as db:
                _backfill_missing_months(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)
                _finalize_stale_drafts(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)

                invoice = _get_or_create_draft_invoice(db, tenant_id, target_year_month)
                if invoice.status != "draft":
                    results.append({"tenant_id": tenant_id, "status": "skipped_not_draft"})
                    continue

                usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
                prices = get_effective_unit_prices(db, tenant_id, year, month)
                calc = calculate_invoice(usage, prices, get_tax_rate(db))

                invoice.subtotal = calc["subtotal"]
                invoice.tax_amount = calc["tax_amount"]
                invoice.total_amount = calc["total_amount"]
                db.commit()

                _replace_line_items(db, invoice.id, calc["line_items"])
            results.append({"tenant_id": tenant_id, "status": "ok", "total_amount": calc["total_amount"]})
        except Exception as e:
            results.append({"tenant_id": tenant_id, "status": "error", "detail": str(e)})
```

を以下に置き換え:

```python
    for tenant_id, schema, influxdb_org_id, influxdb_token in tenant_infos:
        try:
            with SessionLocal() as db:
                tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()

                _backfill_missing_months(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)
                _finalize_stale_drafts(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)

                invoice = _get_or_create_draft_invoice(db, tenant_id, target_year_month)
                if invoice.status != "draft":
                    results.append({"tenant_id": tenant_id, "status": "skipped_not_draft"})
                    continue

                usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
                prices = get_effective_unit_prices(db, tenant_id, year, month)
                calc = calculate_invoice(usage, prices, get_tax_rate(db))

                invoice.subtotal = calc["subtotal"]
                invoice.tax_amount = calc["tax_amount"]
                invoice.total_amount = calc["total_amount"]
                db.commit()

                _replace_line_items(db, invoice.id, calc["line_items"])
                check_and_notify_bill_shock(db, tenant, schema, invoice)
            results.append({"tenant_id": tenant_id, "status": "ok", "total_amount": calc["total_amount"]})
        except Exception as e:
            results.append({"tenant_id": tenant_id, "status": "error", "detail": str(e)})
```

（`tenant`は当月draftの経路でのみ使うため、`_backfill_missing_months`・`_finalize_stale_drafts`より前に取得しておくだけで、それら自体には渡さない。）

- [ ] **Step 8: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_billing_batch.py -v`
Expected: PASS（全件）

- [ ] **Step 9: コミット**

```bash
git add core-api/app/services/bill_shock.py core-api/app/services/billing_batch.py core-api/tests/test_bill_shock.py core-api/tests/test_billing_batch.py
git commit -m "feat: ビルショック通知の判定ロジックを日次バッチに統合"
```

---

### Task 5: API（PF管理者向けデフォルト設定・テナント個別上書き）

**Files:**
- Modify: `core-api/app/routers/platform.py`
- Modify: `core-api/app/routers/billing.py`
- Test: `core-api/tests/test_platform_billing_defaults.py`（デフォルト設定のテスト追加）
- Test: `core-api/tests/test_billing_api.py`（テナント個別上書きのテスト追加）

**Interfaces:**
- Consumes: `get_default_bill_shock_threshold`, `set_default_bill_shock_threshold`, `get_effective_bill_shock_threshold`, `validate_bill_shock_threshold`（Task 2）
- Produces:
  - `GET /platform/billing/bill-shock-threshold` → `{ "default_threshold_amount": "50000" | null }`
  - `PUT /platform/billing/bill-shock-threshold` ← 同形
  - `GET /tenants/{tenant_id}/billing/bill-shock-threshold` → `{ "threshold_amount": "50000" | null, "is_default": true|false }`
  - `PUT /tenants/{tenant_id}/billing/bill-shock-threshold` ← `{ "threshold_amount": "50000" | null }`

- [ ] **Step 1: 失敗するテストを書く（PF管理者向けデフォルト設定）**

`core-api/tests/test_platform_billing_defaults.py`の末尾に追記（ファイル冒頭のimportに`get_default_bill_shock_threshold`等は不要 — 既存の`patch("app.routers.platform....")`パターンを使う）:

```python
def test_get_bill_shock_threshold_requires_platform_auth():
    resp = client.get("/platform/billing/bill-shock-threshold")
    assert resp.status_code == 401


def test_get_bill_shock_threshold_returns_current_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_bill_shock_threshold", return_value=50000):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            "/platform/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": "50000"}


def test_get_bill_shock_threshold_returns_null_when_unset():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_bill_shock_threshold", return_value=None):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            "/platform/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": None}


def test_put_bill_shock_threshold_updates_and_returns_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_bill_shock_threshold") as mock_set, \
         patch("app.routers.platform.write_audit_log") as mock_audit:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/billing/bill-shock-threshold",
            json={"default_threshold_amount": "60000"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": "60000"}
    mock_set.assert_called_once_with(mock_db, 60000)
    assert mock_audit.called
    assert mock_db.commit.called


def test_put_bill_shock_threshold_accepts_null_to_unset():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_bill_shock_threshold") as mock_set, \
         patch("app.routers.platform.write_audit_log"):
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/billing/bill-shock-threshold",
            json={"default_threshold_amount": None},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": None}
    mock_set.assert_called_once_with(mock_db, None)


def test_put_bill_shock_threshold_rejects_invalid_value():
    with patch("app.routers.platform.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/billing/bill-shock-threshold",
            json={"default_threshold_amount": "not-a-number"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422
```

ファイル冒頭の`_session_ctx`・`_platform_token`・`client`が既に定義されていることを確認する（既存の`test_get_tax_rate_*`テストと同じヘルパーを使う）。無ければ既存のヘルパー定義箇所を確認して流用する。

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_platform_billing_defaults.py -k bill_shock -v`
Expected: FAIL（404、対応するルートが無い）

- [ ] **Step 3: `platform.py`に実装を追加する**

`core-api/app/routers/platform.py`の以下のimport行:

```python
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_unit_prices,
    get_tax_rate,
    set_default_unit_prices,
    set_tax_rate,
    validate_tax_rate,
    validate_unit_price,
    ITEM_KEYS,
)
```

を以下に置き換え:

```python
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_bill_shock_threshold,
    get_default_unit_prices,
    get_tax_rate,
    set_default_bill_shock_threshold,
    set_default_unit_prices,
    set_tax_rate,
    validate_bill_shock_threshold,
    validate_tax_rate,
    validate_unit_price,
    ITEM_KEYS,
)
```

`class TaxRateItem(BaseModel):`の直後に追記:

```python
class BillShockThresholdItem(BaseModel):
    default_threshold_amount: str | None = None
```

ファイル末尾（`update_billing_tax_rate`関数の後）に追記:

```python


@router.get("/billing/bill-shock-threshold", response_model=BillShockThresholdItem)
def get_billing_bill_shock_threshold(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        amount = get_default_bill_shock_threshold(db)
    return {"default_threshold_amount": str(amount) if amount is not None else None}


@router.put("/billing/bill-shock-threshold", response_model=BillShockThresholdItem)
def update_billing_bill_shock_threshold(body: BillShockThresholdItem, payload: dict = Depends(_require_platform)):
    try:
        amount = validate_bill_shock_threshold(body.default_threshold_amount or "")
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        set_default_bill_shock_threshold(db, amount)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "update_billing_bill_shock_threshold",
                        resource_type="billing_settings",
                        detail={"default_threshold_amount": amount})
        db.commit()

    return {"default_threshold_amount": str(amount) if amount is not None else None}
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_platform_billing_defaults.py -v`
Expected: PASS（全件）

- [ ] **Step 5: 失敗するテストを書く（テナント個別上書き）**

`core-api/tests/test_billing_api.py`の末尾（`test_correct_tenant_invoice_returns_delta_when_corrected`の後）に追記:

```python
def test_get_tenant_bill_shock_threshold_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/bill-shock-threshold")
    assert resp.status_code == 401


def test_get_tenant_bill_shock_threshold_returns_override_when_set():
    tenant = MagicMock(bill_shock_threshold_amount=70000)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.get_effective_bill_shock_threshold", return_value=70000):
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": "70000", "is_default": False}


def test_get_tenant_bill_shock_threshold_returns_default_when_no_override():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.get_effective_bill_shock_threshold", return_value=50000):
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": "50000", "is_default": True}


def test_get_tenant_bill_shock_threshold_tenant_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_put_tenant_bill_shock_threshold_sets_override():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            json={"threshold_amount": "80000"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": "80000"}
    assert tenant.bill_shock_threshold_amount == 80000
    assert mock_db.commit.called


def test_put_tenant_bill_shock_threshold_clears_override_with_null():
    tenant = MagicMock(bill_shock_threshold_amount=80000)
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            json={"threshold_amount": None},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": None}
    assert tenant.bill_shock_threshold_amount is None


def test_put_tenant_bill_shock_threshold_rejects_invalid_value():
    tenant = MagicMock()
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            json={"threshold_amount": "-5"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422
```

- [ ] **Step 6: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_billing_api.py -k bill_shock -v`
Expected: FAIL（404）

- [ ] **Step 7: `core-api/app/schemas/billing.py`にスキーマを追加する**

`core-api/app/schemas/billing.py`の末尾に追記:

```python


class BillShockThresholdOut(BaseModel):
    threshold_amount: str | None
    is_default: bool


class BillShockThresholdSet(BaseModel):
    threshold_amount: str | None = None
```

- [ ] **Step 8: `billing.py`ルーターに実装を追加する**

`core-api/app/routers/billing.py`の以下のimport行:

```python
from app.schemas.billing import InvoiceOut, UnitPriceOut, UnitPriceSet
```

を以下に置き換え:

```python
from app.schemas.billing import BillShockThresholdOut, BillShockThresholdSet, InvoiceOut, UnitPriceOut, UnitPriceSet
```

以下のimport行:

```python
from app.services.billing import (
    InvalidEffectiveDateError,
    InvalidUnitPriceError,
    get_effective_unit_prices,
    get_invoice_detail_aggregated,
    list_invoices_aggregated,
    set_unit_price,
    validate_unit_price,
)
```

を以下に置き換え:

```python
from app.services.billing import (
    InvalidEffectiveDateError,
    InvalidUnitPriceError,
    get_effective_bill_shock_threshold,
    get_effective_unit_prices,
    get_invoice_detail_aggregated,
    list_invoices_aggregated,
    set_unit_price,
    validate_bill_shock_threshold,
    validate_unit_price,
)
```

ファイル末尾（`correct_tenant_invoice`関数の後）に追記:

```python


@router.get("/bill-shock-threshold", response_model=BillShockThresholdOut)
def get_tenant_bill_shock_threshold(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        effective = get_effective_bill_shock_threshold(db, tenant)
        is_default = tenant.bill_shock_threshold_amount is None
    return {
        "threshold_amount": str(effective) if effective is not None else None,
        "is_default": is_default,
    }


@router.put("/bill-shock-threshold", response_model=BillShockThresholdSet)
def update_tenant_bill_shock_threshold(tenant_id: str, body: BillShockThresholdSet, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    try:
        amount = validate_bill_shock_threshold(body.threshold_amount or "")
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant.bill_shock_threshold_amount = amount
        db.commit()

    log_audit("platform", payload["sub"], payload["email"], "update_tenant_bill_shock_threshold",
              tenant_id=tenant_id, resource_type="tenant",
              detail={"threshold_amount": amount})
    return {"threshold_amount": str(amount) if amount is not None else None}
```

- [ ] **Step 9: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_billing_api.py tests/test_platform_billing_defaults.py -v`
Expected: PASS（全件）

- [ ] **Step 10: コミット**

```bash
git add core-api/app/routers/platform.py core-api/app/routers/billing.py core-api/app/schemas/billing.py core-api/tests/test_platform_billing_defaults.py core-api/tests/test_billing_api.py
git commit -m "feat: ビルショック通知しきい値のPF管理者向けAPIを追加"
```

---

### Task 6: UI（PF管理者向けデフォルト設定・テナント個別上書き）＋ドキュメント更新

**Files:**
- Modify: `admin-ui/js/api.js`
- Modify: `platform-ui/platform-settings.html`
- Modify: `platform-ui/tenant.html`
- Modify: `admin-ui/static/tailwind.css`（`npm run build:css`で再生成）
- Modify: `docs/superpowers/specs/2026-09-10-billing-calculation-design.md`（5節の該当行を解決済みに更新）

このタスクにはTDDサイクル（テストなし・手動UI確認）を適用する。既存の`platform-ui/tenant.html`にはUIの自動テストが無く、Alpine.js側は既存パターンの踏襲で足りるため、手動確認のみとする。

- [ ] **Step 1: `admin-ui/js/api.js`にAPI関数を追加する**

`admin-ui/js/api.js`の以下の行:

```javascript
        getBillingTaxRate: () => request('GET', '/platform/billing/tax-rate'),
        updateBillingTaxRate: (body) => request('PUT', '/platform/billing/tax-rate', body),
```

を以下に置き換え:

```javascript
        getBillingTaxRate: () => request('GET', '/platform/billing/tax-rate'),
        updateBillingTaxRate: (body) => request('PUT', '/platform/billing/tax-rate', body),
        getBillingBillShockThreshold: () => request('GET', '/platform/billing/bill-shock-threshold'),
        updateBillingBillShockThreshold: (body) => request('PUT', '/platform/billing/bill-shock-threshold', body),
```

- [ ] **Step 2: `platform-ui/platform-settings.html`にデフォルトしきい値カードを追加する**

`<script src="/admin/js/api.js?v=13">`を`<script src="/admin/js/api.js?v=14">`に変更する（Step 1でapi.jsに新規関数が増えたためキャッシュバスターを上げる）。

`消費税率`カードの`</div>`（`<p x-show="taxRateSaved" ...>消費税率を保存しました</p>`の直後、`</div>`の前の閉じタグ）の後に、新しいカードを追加:

```html

    <div x-show="!loading" class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mt-6">
      <h2 class="text-base font-semibold text-gray-800 mb-1">ビルショック通知しきい値（デフォルト）</h2>
      <p class="text-xs text-gray-400 mb-4">当月の請求合計金額がこの金額を超えたら、PF管理者・テナント管理者へメールで通知します。空欄の場合は通知しません。テナントごとに個別の上書き値を設定することもできます（テナント詳細画面）。</p>
      <div x-show="bsThresholdError" class="mb-3 text-sm text-red-600" x-text="bsThresholdError"></div>
      <div class="flex items-center justify-between py-2">
        <p class="text-sm font-medium text-gray-700">しきい値（円）</p>
        <input type="text" x-model="bsThreshold" placeholder="未設定（通知しない）"
               class="border border-gray-300 rounded px-2 py-1.5 text-sm w-32 text-right">
      </div>
      <button @click="saveBsThreshold()" :disabled="savingBsThreshold || bsThresholdLoadFailed"
              class="mt-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-4 py-2 rounded text-sm">
        <span x-show="!savingBsThreshold">保存</span>
        <span x-show="savingBsThreshold">保存中...</span>
      </button>
      <p x-show="bsThresholdSaved" class="mt-3 text-sm text-green-600">しきい値を保存しました</p>
    </div>
```

`taxRateLoadFailed: false,`の行の直後に追記:

```javascript
        bsThreshold: '',
        bsThresholdError: '',
        bsThresholdSaved: false,
        savingBsThreshold: false,
        bsThresholdLoadFailed: false,
```

`init()`メソッド内、税率読み込みの`try { ... } catch(e) { this.taxRateError = ...; this.taxRateLoadFailed = true; }`ブロックの直後（`this.loading = false;`の前）に、新しい独立したtry/catchを追記:

```javascript
          try {
            const bs = await api.platform.getBillingBillShockThreshold();
            this.bsThreshold = bs.default_threshold_amount ?? '';
          } catch(e) {
            this.bsThresholdError = e.message;
            this.bsThresholdLoadFailed = true;
          }
```

`saveTaxRate()`メソッドの後に、新しいメソッドを追記:

```javascript
        async saveBsThreshold() {
          this.bsThresholdSaved = false;
          this.bsThresholdError = '';
          this.savingBsThreshold = true;
          try {
            const result = await api.platform.updateBillingBillShockThreshold({
              default_threshold_amount: this.bsThreshold === '' ? null : String(this.bsThreshold),
            });
            this.bsThreshold = result.default_threshold_amount ?? '';
            this.bsThresholdSaved = true;
            setTimeout(() => this.bsThresholdSaved = false, 3000);
          } catch(e) { this.bsThresholdError = e.message; }
          finally { this.savingBsThreshold = false; }
        },
```

- [ ] **Step 3: `platform-ui/tenant.html`にテナント個別上書き入力欄を追加する**

`<script src="/admin/js/api.js?v=10">`はそのままでよい（このファイルはこの後も既存の`api.request`ジェネリック呼び出しパターンを使い、api.jsに新規関数を追加しないため）。

課金単価テーブルの`</div>`（`<h3 class="font-medium text-gray-700 mb-3 mt-8">請求書</h3>`の直前の`</div>`）の後に追加:

```html

        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mt-4">
          <p class="text-sm font-medium text-gray-700 mb-1">ビルショック通知しきい値（このテナントの上書き）</p>
          <p class="text-xs text-gray-400 mb-3">空欄で保存するとデフォルト値に戻ります。</p>
          <div class="flex items-center gap-2">
            <input type="text" x-model="bsThreshold" :placeholder="'現在の実効値: ' + (bsThresholdIsDefault ? '¥' + bsThreshold + '（デフォルト）' : '¥' + bsThreshold)"
                   class="border border-gray-300 rounded px-2 py-1.5 text-sm w-40">
            <button @click="saveBsThreshold()" :disabled="savingBsThreshold"
                    class="bg-blue-600 text-white text-sm px-3 py-1.5 rounded disabled:bg-blue-300">保存</button>
          </div>
          <p x-show="bsThresholdSaved" class="mt-2 text-xs text-green-600">保存しました</p>
        </div>
```

`invoices: [], invoicesLoading: false,`の行の直前に追記:

```javascript
        bsThreshold: '', bsThresholdIsDefault: true, savingBsThreshold: false, bsThresholdSaved: false,
```

`loadBillingPrices()`メソッド内の`this.loadInvoices();`の行の直後に追記:

```javascript
          this.loadBsThreshold();
```

`loadInvoices()`メソッドの後に、新しいメソッドを追記:

```javascript
        async loadBsThreshold() {
          try {
            const r = await api.request('GET', `/tenants/${tenantId}/billing/bill-shock-threshold`);
            this.bsThreshold = r.threshold_amount ?? '';
            this.bsThresholdIsDefault = r.is_default;
          } catch(e) { this.error = e.message; }
        },

        async saveBsThreshold() {
          this.bsThresholdSaved = false;
          this.savingBsThreshold = true;
          try {
            await api.request('PUT', `/tenants/${tenantId}/billing/bill-shock-threshold`, {
              threshold_amount: this.bsThreshold === '' ? null : String(this.bsThreshold),
            });
            await this.loadBsThreshold();
            this.bsThresholdSaved = true;
            setTimeout(() => this.bsThresholdSaved = false, 3000);
          } catch(e) { this.error = e.message; }
          finally { this.savingBsThreshold = false; }
        },
```

- [ ] **Step 4: HTMLタグの整合性を確認する**

Run:
```bash
python3 -c "
import re
for path in ['platform-ui/platform-settings.html', 'platform-ui/tenant.html']:
    content = open(path, encoding='utf-8').read()
    for tag in ['div','button','template']:
        opens = len(re.findall(r'<'+tag+r'[\s>]', content)) - len(re.findall(r'<'+tag+r'[^>]*/>', content))
        closes = len(re.findall(r'</'+tag+r'>', content))
        print(path, tag, opens, closes)
"
```
Expected: 各`tag`について`opens == closes`

- [ ] **Step 5: Tailwind CSSをビルドする**

Run: `npm run build:css`
Expected: `Done in ...ms.`（エラーなし）

- [ ] **Step 6: 手動UI確認**

開発環境でcore-apiを起動し、以下を確認する:
1. `/iotairx-console/platform-settings.html`で「ビルショック通知しきい値（デフォルト）」カードが表示され、値を保存・再読込できること。
2. `/iotairx-console/tenants.html`からテナント詳細を開き「単価設定」タブで、テナント個別のしきい値上書き欄が表示され、空欄保存で「デフォルトに戻る」こと。
3. ブラウザのコンソールにJSエラーが出ていないこと。

（このステップは自動テストではなく手動確認。実施できない場合はその旨を明示的に報告する。）

- [ ] **Step 7: 設計仕様書の5節を更新する**

`docs/superpowers/specs/2026-09-10-billing-calculation-design.md`の以下の行:

```markdown
| 想定利用量閾値のデフォルト値（数値そのもの） | 画面から変更可能にする方針は確定（3節・2.5節）。デフォルト初期値をいくつにするかは次回決める |
```

を削除し、`**解決済み（2026-09-11）:**`の行の後に追記:

```markdown
- ビルショック通知 → `billing_settings.default_bill_shock_threshold_amount`（デフォルト）＋`tenants.bill_shock_threshold_amount`（テナント個別上書き）で実装。当月draft請求書の合計金額が実効しきい値を超えたら、監査ログ記録＋メール送信（`app/services/mailer.py`、core-apiに新規実装）で月1回のみ通知する（`billing_invoices.bill_shock_notified_at`でdedup）。デフォルト初期値は未設定（PF管理者が画面から設定するまで通知しない）。
```

- [ ] **Step 8: コミット**

```bash
git add admin-ui/js/api.js platform-ui/platform-settings.html platform-ui/tenant.html admin-ui/static/tailwind.css docs/superpowers/specs/2026-09-10-billing-calculation-design.md
git commit -m "feat: ビルショック通知しきい値のUIを追加"
```

---

### Task 7: 最終確認

**Files:** なし（既存ファイルの検証のみ）

- [ ] **Step 1: core-api全体のテストスイートを実行する**

Run: `cd core-api && python -m pytest tests/ -v`
Expected: 既存の無関係なベースライン失敗（`test_auth.py::test_hash_and_verify_password`等、bcryptバージョン起因の既知の7件）を除き、全件PASS。

- [ ] **Step 2: `docker-compose config`で構文検証する**

Run: `docker compose config --quiet`
Expected: エラーなし（`SMTP_*`環境変数追加後もYAML構文が正しいことを確認）

- [ ] **Step 3: 完了報告**

全タスクの完了をユーザーに報告し、pushしてよいか確認する。
