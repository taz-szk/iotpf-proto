# テレメトリデータ保持ルール機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** InfluxDBに蓄積されるテナントのテレメトリデータの保持期間を、PF管理者のデフォルト値＋テナントごとの個別設定（テナント管理者・PF管理者双方が変更可能）で制御できるようにする。

**Architecture:** 既存の`billing_settings`/`tenants`テーブルにカラムを追加するだけで新規テーブルは作らない。InfluxDBネイティブのバケット単位retention rule機能を使い、自前の削除バッチは書かない。テナント開通時に実効デフォルト値でバケットを先行作成し、日次バッチ（新規`app/services/data_retention.py`、既存の`billing_batch`/`audit purge worker`と同じdaemon threadパターン）が全アクティブテナントの実効保持日数をInfluxDBに同期する。

**Tech Stack:** FastAPI, SQLAlchemy ORM, Pydantic, `httpx`（InfluxDB REST API直呼び出し）, Alpine.js, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-09-12-data-retention-design.md`

## Global Constraints

- 削除の実行はInfluxDBネイティブのバケット単位retention rule機能を使う。自前の削除クエリ・削除バッチは書かない。
- 保持期間の下限は60日。下限を下回る値はバリデーションエラー（422）にする。上限は設けない。
- PF管理者デフォルト値・テナント個別上書きのいずれの変更も、日次バッチでのみInfluxDBに反映する（APIハンドラからの即時InfluxDB呼び出しはしない）。
- テナント個別の保持期間は、テナント管理者（`role='admin'`のセルフサービス）とPF管理者（代理設定）の両方が変更できる。
- `billing_settings.default_retention_days`は`NOT NULL DEFAULT 365`（保持期間の無効化＝無期限保持は許可しない）。`tenants.data_retention_days`は`NULL`許容（NULL＝個別上書きなし、デフォルト値を使う）。
- 日次サイクル内の起動順序は「課金バッチワーカー→保持期間同期ワーカー」の順にする（`main.py`での`start_billing_batch_worker()`呼び出しの後に`start_data_retention_sync_worker()`を呼ぶ）。

---

### Task 1: スキーマ変更（テーブルカラム追加）

**Files:**
- Modify: `core-api/app/database.py`（末尾、`migrate_add_bill_shock_threshold_columns`の後に追記）
- Modify: `core-api/app/models/billing.py`（`BillingSettings`にカラム追加）
- Modify: `core-api/app/models/public.py`（`Tenant`にカラム追加）
- Modify: `core-api/app/main.py`（マイグレーション関数のimport・起動時実行リストに追加）
- Test: `core-api/tests/test_db.py`（新規マイグレーション関数のテスト追加）
- Test: `core-api/tests/test_billing_settings_model.py`（カラム追加分のアサーション更新）

**Interfaces:**
- Produces: `BillingSettings.default_retention_days`（`int`、`NOT NULL DEFAULT 365`）, `Tenant.data_retention_days`（`int | None`）。以降のタスクはこれらのカラムを直接参照する。

- [ ] **Step 1: 失敗するテストを書く（マイグレーション関数）**

`core-api/tests/test_db.py`の末尾に追記:

```python
def test_migrate_add_data_retention_columns_executes():
    from app.database import migrate_add_data_retention_columns
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        migrate_add_data_retention_columns()
    sql_calls = _sql_text(mock_conn.execute.call_args_list)
    assert "billing_settings" in sql_calls and "default_retention_days" in sql_calls
    assert "DEFAULT 365" in sql_calls
    assert "tenants" in sql_calls and "data_retention_days" in sql_calls
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py::test_migrate_add_data_retention_columns_executes -v`
Expected: FAIL（`ImportError: cannot import name 'migrate_add_data_retention_columns'`）

- [ ] **Step 3: `database.py`にマイグレーション関数を追加する**

`core-api/app/database.py`の末尾（`migrate_add_bill_shock_threshold_columns`関数の後）に追記:

```python


def migrate_add_data_retention_columns() -> None:
    """テレメトリデータ保持期間の設定用カラムを追加する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE billing_settings
            ADD COLUMN IF NOT EXISTS default_retention_days INTEGER NOT NULL DEFAULT 365
        """))
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS data_retention_days INTEGER
        """))
        conn.commit()
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py::test_migrate_add_data_retention_columns_executes -v`
Expected: PASS

- [ ] **Step 5: モデルにカラムを追加する**

`core-api/app/models/billing.py`の`BillingSettings`クラスを以下に置き換え:

```python
class BillingSettings(Base):
    __tablename__ = "billing_settings"
    id = Column(Integer, primary_key=True, default=1)
    tax_rate = Column(Numeric(5, 4), nullable=False)
    default_bill_shock_threshold_amount = Column(Integer, nullable=True)
    default_retention_days = Column(Integer, nullable=False, default=365)
```

`core-api/app/models/public.py`の`Tenant`クラスの`bill_shock_threshold_amount`行の後に1行追加:

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
    data_retention_days = Column(Integer, nullable=True)
```

- [ ] **Step 6: `test_billing_settings_model.py`を更新する**

`core-api/tests/test_billing_settings_model.py`の`assert columns == {"id", "tax_rate", "default_bill_shock_threshold_amount"}`を以下に変更:

```python
    assert columns == {"id", "tax_rate", "default_bill_shock_threshold_amount", "default_retention_days"}
```

- [ ] **Step 7: main.pyにマイグレーションを配線する**

`core-api/app/main.py`の以下の行:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns
```

を以下に置き換え:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns, migrate_add_data_retention_columns
```

同ファイルの以下の行:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns):
```

を以下に置き換え:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns, migrate_add_data_retention_columns):
```

- [ ] **Step 8: 全テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py tests/test_billing_settings_model.py tests/test_billing_models.py -v`
Expected: PASS（全件）

- [ ] **Step 9: コミット**

```bash
git add core-api/app/database.py core-api/app/models/billing.py core-api/app/models/public.py core-api/app/main.py core-api/tests/test_db.py core-api/tests/test_billing_settings_model.py
git commit -m "feat: データ保持期間設定用のカラムを追加(billing_settings/tenants)"
```

---

### Task 2: 保持日数の解決・設定サービス関数

**Files:**
- Modify: `core-api/app/services/billing.py`（末尾に追記）
- Test: `core-api/tests/test_data_retention_settings.py`（新規）

**Interfaces:**
- Consumes: Task 1の`BillingSettings.default_retention_days`, `Tenant.data_retention_days`
- Produces:
  - `DEFAULT_RETENTION_DAYS: int`（モジュール定数、365）
  - `MIN_RETENTION_DAYS: int`（モジュール定数、60）
  - `get_default_retention_days(db: Session) -> int`
  - `set_default_retention_days(db: Session, days: int) -> None`
  - `get_effective_retention_days(db: Session, tenant: Tenant) -> int`（Task 3・Task 4・Task 5が使用）
  - `validate_retention_days(value_str: str) -> int`（Task 5が使用。既存の`InvalidUnitPriceError`を再利用。**しきい値バリデーションと違い、空文字列は許可しない**——常に具体的な整数値を要求する）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_data_retention_settings.py`を新規作成:

```python
from unittest.mock import MagicMock
import pytest

from app.models.billing import BillingSettings
from app.services.billing import (
    DEFAULT_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    InvalidUnitPriceError,
    get_default_retention_days,
    get_effective_retention_days,
    set_default_retention_days,
    validate_retention_days,
)


def test_default_retention_days_constant_is_365():
    assert DEFAULT_RETENTION_DAYS == 365


def test_min_retention_days_constant_is_60():
    assert MIN_RETENTION_DAYS == 60


def test_get_default_retention_days_returns_configured_value():
    row = MagicMock(spec=BillingSettings)
    row.default_retention_days = 180
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_default_retention_days(mock_db) == 180


def test_get_default_retention_days_falls_back_when_no_row():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_default_retention_days(mock_db) == DEFAULT_RETENTION_DAYS


def test_set_default_retention_days_updates_existing_row():
    row = MagicMock(spec=BillingSettings)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    set_default_retention_days(mock_db, 180)
    assert row.default_retention_days == 180
    mock_db.add.assert_not_called()
    mock_db.commit.assert_called_once()


def test_set_default_retention_days_creates_row_when_missing():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    set_default_retention_days(mock_db, 180)
    assert mock_db.add.called
    added = mock_db.add.call_args[0][0]
    assert added.id == 1
    assert added.default_retention_days == 180
    mock_db.commit.assert_called_once()


def test_get_effective_retention_days_uses_tenant_override():
    tenant = MagicMock(data_retention_days=90)
    mock_db = MagicMock()
    assert get_effective_retention_days(mock_db, tenant) == 90
    mock_db.query.assert_not_called()


def test_get_effective_retention_days_falls_back_to_default():
    tenant = MagicMock(data_retention_days=None)
    default_row = MagicMock(spec=BillingSettings)
    default_row.default_retention_days = 365
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = default_row
    assert get_effective_retention_days(mock_db, tenant) == 365


def test_validate_retention_days_accepts_valid_value():
    assert validate_retention_days("180") == 180


def test_validate_retention_days_accepts_exact_minimum():
    assert validate_retention_days("60") == 60


def test_validate_retention_days_rejects_below_minimum():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("59")


def test_validate_retention_days_rejects_empty_string():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("")


def test_validate_retention_days_rejects_non_integer():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("not-a-number")


def test_validate_retention_days_rejects_decimal():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("180.5")


def test_validate_retention_days_accepts_large_value_no_upper_bound():
    assert validate_retention_days("36500") == 36500
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_data_retention_settings.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: `billing.py`に実装を追加する**

`core-api/app/services/billing.py`の末尾に追記:

```python


DEFAULT_RETENTION_DAYS = 365
MIN_RETENTION_DAYS = 60


def get_default_retention_days(db: Session) -> int:
    """設定済みのデフォルトデータ保持日数を返す。行が無ければDEFAULT_RETENTION_DAYSを返す
    （マイグレーションで常に1行存在するはずだが、念のためのフォールバック）。"""
    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    if row is None:
        return DEFAULT_RETENTION_DAYS
    return row.default_retention_days


def set_default_retention_days(db: Session, days: int) -> None:
    """デフォルトのデータ保持日数を更新する（シングルトン行、常にid=1）。"""
    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    if row is None:
        db.add(BillingSettings(id=1, tax_rate=DEFAULT_TAX_RATE, default_retention_days=days))
    else:
        row.default_retention_days = days
    db.commit()


def get_effective_retention_days(db: Session, tenant) -> int:
    """テナント個別の保持日数上書きがあればそれを、無ければデフォルト値を返す。
    しきい値と異なり、保持期間は必ず具体的な日数を持つ（Noneを返すことはない）。"""
    if tenant.data_retention_days is not None:
        return tenant.data_retention_days
    return get_default_retention_days(db)


def validate_retention_days(value_str: str) -> int:
    """保持日数の文字列をintに変換する。60未満・空文字列・非整数はInvalidUnitPriceErrorを投げる。
    上限は設けない。"""
    try:
        days = int(value_str)
    except (ValueError, TypeError):
        raise InvalidUnitPriceError("retention_days must be an integer")
    if str(days) != value_str.lstrip("+"):
        raise InvalidUnitPriceError("retention_days must be an integer")
    if days < MIN_RETENTION_DAYS:
        raise InvalidUnitPriceError(f"retention_days must be at least {MIN_RETENTION_DAYS}")
    return days
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_data_retention_settings.py -v`
Expected: PASS（全15件）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/billing.py core-api/tests/test_data_retention_settings.py
git commit -m "feat: データ保持日数の解決・設定ロジックを追加"
```

---

### Task 3: テナント開通時のInfluxDBバケット先行作成

**Files:**
- Modify: `core-api/app/services/tenant.py`（`create_influxdb_bucket`を追加）
- Modify: `core-api/app/routers/tenants.py`（`create_tenant`に組み込み）
- Test: `core-api/tests/test_tenant_service.py`（新規、`create_influxdb_bucket`単体テスト）
- Test: `core-api/tests/test_tenants.py`（`create_tenant`が`create_influxdb_bucket`を呼ぶことを検証するテスト追加）

**Interfaces:**
- Consumes: `get_default_retention_days`（Task 2）
- Produces: `create_influxdb_bucket(org_id: str, admin_token: str, retention_days: int) -> None`（Task 4は使わない。テナント開通時専用）

- [ ] **Step 1: 失敗するテストを書く（`create_influxdb_bucket`本体）**

`core-api/tests/test_tenant_service.py`を新規作成:

```python
from unittest.mock import patch, MagicMock

from app.services.tenant import create_influxdb_bucket


def test_create_influxdb_bucket_sends_retention_rule():
    mock_resp = MagicMock(status_code=201)
    with patch("app.services.tenant.httpx.post", return_value=mock_resp) as mock_post:
        create_influxdb_bucket("org-1", "admin-token", 180)

    mock_post.assert_called_once()
    call = mock_post.call_args
    assert "/api/v2/buckets" in call.args[0]
    assert call.kwargs["json"]["orgID"] == "org-1"
    assert call.kwargs["json"]["name"] == "telemetry"
    assert call.kwargs["json"]["retentionRules"] == [{"type": "expire", "everySeconds": 180 * 86400}]
    assert call.kwargs["headers"]["Authorization"] == "Token admin-token"


def test_create_influxdb_bucket_ignores_already_exists_response():
    mock_resp = MagicMock(status_code=422)
    with patch("app.services.tenant.httpx.post", return_value=mock_resp):
        create_influxdb_bucket("org-1", "admin-token", 180)  # 例外を投げなければ成功


def test_create_influxdb_bucket_raises_on_other_errors():
    mock_resp = MagicMock(status_code=500)
    mock_resp.raise_for_status.side_effect = Exception("server error")
    with patch("app.services.tenant.httpx.post", return_value=mock_resp):
        try:
            create_influxdb_bucket("org-1", "admin-token", 180)
            assert False, "expected exception"
        except Exception:
            pass
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_service.py -v`
Expected: FAIL（`ImportError: cannot import name 'create_influxdb_bucket'`）

- [ ] **Step 3: `tenant.py`に実装を追加する**

`core-api/app/services/tenant.py`の`create_influxdb_token_for_org`関数の後（`def teardown_tenant`の前）に追記:

```python
def create_influxdb_bucket(org_id: str, admin_token: str, retention_days: int) -> None:
    """telemetryバケットを指定の保持日数のretention ruleで作成する。
    既に存在する場合（ingestion-serviceの遅延作成と競合した場合等）は何もしない。"""
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/buckets",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={
            "orgID": org_id, "name": "telemetry",
            "retentionRules": [{"type": "expire", "everySeconds": retention_days * 86400}],
        },
        timeout=10.0,
    )
    if resp.status_code not in (201, 422):
        resp.raise_for_status()
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_service.py -v`
Expected: PASS（全3件）

- [ ] **Step 5: 失敗するテストを書く（`create_tenant`への組み込み）**

`core-api/tests/test_tenants.py`の`test_create_tenant_seeds_default_prices`関数の後に追記:

```python
def test_create_tenant_creates_influxdb_bucket_with_default_retention(client):
    tenant_id = str(uuid.uuid4())

    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID(tenant_id)
    mock_tenant.name = "Test Tenant"
    mock_tenant.slug = "test-tenant"
    mock_tenant.status = "active"
    mock_tenant.grafana_org_id = "42"
    from datetime import datetime, timezone
    mock_tenant.created_at = datetime.now(timezone.utc)

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.setup_tenant", return_value=("influx-org-id-001", "influx-token-001", 42)), \
         patch("app.routers.tenants.seed_tenant_default_prices"), \
         patch("app.routers.tenants.create_influxdb_bucket") as mock_create_bucket, \
         patch("app.routers.tenants.get_default_retention_days", return_value=365), \
         patch("app.routers.tenants.write_audit_log"), \
         patch("app.routers.tenants.uuid.uuid4", return_value=uuid.UUID(tenant_id)), \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "admin@example.com", "type": "platform"}):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.refresh.side_effect = lambda t: setattr(t, 'id', uuid.UUID(tenant_id)) or setattr(t, 'created_at', datetime.now(timezone.utc)) or setattr(t, 'status', 'active')

        resp = client.post(
            "/tenants",
            json={"name": "Test Tenant", "slug": "test-tenant"},
            headers={"Authorization": "Bearer dummy"}
        )

    assert resp.status_code == 201
    mock_create_bucket.assert_called_once()
    call_args = mock_create_bucket.call_args[0]
    assert call_args[0] == "influx-org-id-001"  # org_id
    assert call_args[2] == 365  # retention_days（get_default_retention_daysの戻り値）
```

- [ ] **Step 6: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_tenants.py::test_create_tenant_creates_influxdb_bucket_with_default_retention -v`
Expected: FAIL（`create_tenant`が`create_influxdb_bucket`を呼んでいない）

- [ ] **Step 7: `tenants.py`に組み込む**

`core-api/app/routers/tenants.py`の以下の行:

```python
from app.services.tenant import setup_tenant, teardown_tenant
from app.services.billing import seed_tenant_default_prices
```

を以下に置き換え:

```python
from app.services.tenant import setup_tenant, teardown_tenant, create_influxdb_bucket
from app.services.billing import seed_tenant_default_prices, get_default_retention_days
from app.config import settings
```

`create_tenant`関数内の以下の行:

```python
        seed_tenant_default_prices(db, tenant_id)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "create_tenant", tenant_id=tenant_id, resource_type="tenant", resource_id=body.name)
```

を以下に置き換え:

```python
        seed_tenant_default_prices(db, tenant_id)
        create_influxdb_bucket(org_id, settings.influxdb_admin_token, get_default_retention_days(db))
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "create_tenant", tenant_id=tenant_id, resource_type="tenant", resource_id=body.name)
```

- [ ] **Step 8: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_tenants.py tests/test_tenant_service.py -v`
Expected: PASS（全件）

- [ ] **Step 9: コミット**

```bash
git add core-api/app/services/tenant.py core-api/app/routers/tenants.py core-api/tests/test_tenant_service.py core-api/tests/test_tenants.py
git commit -m "feat: テナント開通時にtelemetryバケットをデフォルト保持期間で先行作成"
```

---

### Task 4: 日次同期ジョブ

**Files:**
- Create: `core-api/app/services/data_retention.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_data_retention_sync.py`（新規）

**Interfaces:**
- Consumes: `get_effective_retention_days`（Task 2）
- Produces:
  - `sync_tenant_retention(org_id: str, admin_token: str, effective_days: int) -> None`
  - `run_daily_retention_sync() -> list[dict]`
  - `start_data_retention_sync_worker() -> None`

- [ ] **Step 1: 失敗するテストを書く（`sync_tenant_retention`）**

`core-api/tests/test_data_retention_sync.py`を新規作成:

```python
from unittest.mock import patch, MagicMock

from app.services.data_retention import sync_tenant_retention, run_daily_retention_sync


def test_sync_tenant_retention_noop_when_bucket_not_found():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {"buckets": []}
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch") as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_not_called()


def test_sync_tenant_retention_noop_when_already_matching():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {
        "buckets": [{"id": "bucket-1", "retentionRules": [{"type": "expire", "everySeconds": 180 * 86400}]}]
    }
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch") as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_not_called()


def test_sync_tenant_retention_patches_when_different():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {
        "buckets": [{"id": "bucket-1", "retentionRules": [{"type": "expire", "everySeconds": 365 * 86400}]}]
    }
    mock_patch_resp = MagicMock(status_code=200)
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch", return_value=mock_patch_resp) as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_called_once()
    call = mock_patch.call_args
    assert "bucket-1" in call.args[0]
    assert call.kwargs["json"]["retentionRules"] == [{"type": "expire", "everySeconds": 180 * 86400}]


def test_sync_tenant_retention_patches_when_no_existing_rule():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {"buckets": [{"id": "bucket-1", "retentionRules": []}]}
    mock_patch_resp = MagicMock(status_code=200)
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch", return_value=mock_patch_resp) as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_called_once()


def test_run_daily_retention_sync_processes_active_tenants_with_influxdb_org():
    tenant_with_org = MagicMock(id="tenant-1", status="active", influxdb_org_id="org-1", data_retention_days=None)
    tenant_without_org = MagicMock(id="tenant-2", status="active", influxdb_org_id=None, data_retention_days=None)

    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = [tenant_with_org, tenant_without_org]

    with patch("app.services.data_retention.SessionLocal", return_value=mock_db), \
         patch("app.services.data_retention.get_effective_retention_days", return_value=365), \
         patch("app.services.data_retention.sync_tenant_retention") as mock_sync:
        results = run_daily_retention_sync()

    mock_sync.assert_called_once()
    assert mock_sync.call_args[0][0] == "org-1"
    assert mock_sync.call_args[0][2] == 365
    assert results == [{"tenant_id": "tenant-1", "status": "ok"}]


def test_run_daily_retention_sync_records_error_without_stopping():
    tenant = MagicMock(id="tenant-1", status="active", influxdb_org_id="org-1", data_retention_days=None)

    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = [tenant]

    with patch("app.services.data_retention.SessionLocal", return_value=mock_db), \
         patch("app.services.data_retention.get_effective_retention_days", return_value=365), \
         patch("app.services.data_retention.sync_tenant_retention", side_effect=Exception("influxdb down")):
        results = run_daily_retention_sync()

    assert results == [{"tenant_id": "tenant-1", "status": "error", "detail": "influxdb down"}]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_data_retention_sync.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.data_retention'`）

- [ ] **Step 3: `data_retention.py`を新規作成する**

`core-api/app/services/data_retention.py`:

```python
import threading
import time

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.public import Tenant
from app.services.billing import get_effective_retention_days


def sync_tenant_retention(org_id: str, admin_token: str, effective_days: int) -> None:
    """指定テナントのtelemetryバケットのretention ruleを実効値に同期する。
    バケットが存在しなければ何もしない（次回テレメトリ受信時に正しい値で作成される）。
    現在のretentionRulesが既に一致していればPATCHしない。"""
    resp = httpx.get(
        f"{settings.influxdb_url}/api/v2/buckets",
        headers={"Authorization": f"Token {admin_token}"},
        params={"orgID": org_id, "name": "telemetry"},
        timeout=10.0,
    )
    resp.raise_for_status()
    buckets = resp.json().get("buckets", [])
    if not buckets:
        return

    bucket = buckets[0]
    desired_seconds = effective_days * 86400
    current_rules = bucket.get("retentionRules", [])
    current_seconds = current_rules[0]["everySeconds"] if current_rules else 0
    if current_seconds == desired_seconds:
        return

    patch_resp = httpx.patch(
        f"{settings.influxdb_url}/api/v2/buckets/{bucket['id']}",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={"retentionRules": [{"type": "expire", "everySeconds": desired_seconds}]},
        timeout=10.0,
    )
    patch_resp.raise_for_status()


def run_daily_retention_sync() -> list[dict]:
    """全アクティブテナント（InfluxDB org未設定のものを除く）について、
    実効保持日数をInfluxDBに同期する。戻り値は各テナントの処理結果。"""
    results: list[dict] = []
    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(Tenant.status == "active").all()
        tenant_infos = [
            (str(t.id), t.influxdb_org_id, get_effective_retention_days(db, t))
            for t in tenants if t.influxdb_org_id
        ]

    for tenant_id, org_id, effective_days in tenant_infos:
        try:
            sync_tenant_retention(org_id, settings.influxdb_admin_token, effective_days)
            results.append({"tenant_id": tenant_id, "status": "ok"})
        except Exception as e:
            results.append({"tenant_id": tenant_id, "status": "error", "detail": str(e)})

    return results


def start_data_retention_sync_worker() -> None:
    """毎日1回、全アクティブテナントのデータ保持期間をInfluxDBに同期する
    バックグラウンドスレッドを起動する。既存のstart_billing_batch_worker/
    start_audit_purge_workerと同じdaemon thread+time.sleep(86400)パターン。"""
    def _loop() -> None:
        while True:
            try:
                results = run_daily_retention_sync()
                for r in results:
                    if r.get("status") != "ok":
                        print(f"[data_retention] tenant {r.get('tenant_id')}: {r}")
            except Exception as e:
                print(f"[data_retention] run failed: {e}")
            time.sleep(86400)
    threading.Thread(target=_loop, daemon=True).start()
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_data_retention_sync.py -v`
Expected: PASS（全6件）

- [ ] **Step 5: main.pyに配線する（起動順序は課金バッチワーカーの後）**

`core-api/app/main.py`の以下の行:

```python
from app.services.billing_batch import start_billing_batch_worker
```

を以下に置き換え:

```python
from app.services.billing_batch import start_billing_batch_worker
from app.services.data_retention import start_data_retention_sync_worker
```

同ファイルの以下の行:

```python
    start_audit_purge_worker()
    start_billing_batch_worker()
```

を以下に置き換え:

```python
    start_audit_purge_worker()
    start_billing_batch_worker()
    start_data_retention_sync_worker()
```

（`start_billing_batch_worker()`の直後に置くことで、Global Constraintsに記載の起動順序の取り決めに従う。）

- [ ] **Step 6: コミット**

```bash
git add core-api/app/services/data_retention.py core-api/app/main.py core-api/tests/test_data_retention_sync.py
git commit -m "feat: データ保持期間の日次InfluxDB同期ジョブを追加"
```

---

### Task 5: API（PF管理者向けデフォルト設定・テナント個別上書き・テナント管理者セルフサービス）

**Files:**
- Modify: `core-api/app/routers/platform.py`
- Modify: `core-api/app/routers/tenants.py`
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_platform_billing_defaults.py`（デフォルト設定のテスト追加）
- Test: `core-api/tests/test_tenants.py`（テナント個別上書きのテスト追加）
- Test: `core-api/tests/test_tenant_portal_data_retention.py`（新規、テナント管理者セルフサービスのテスト）

**Interfaces:**
- Consumes: `get_default_retention_days`, `set_default_retention_days`, `get_effective_retention_days`, `validate_retention_days`（Task 2）
- Produces:
  - `GET /platform/data-retention` → `{ "default_retention_days": "365" }`
  - `PUT /platform/data-retention` ← 同形
  - `GET /tenants/{tenant_id}/data-retention` → `{ "retention_days": "365", "is_default": true|false }`
  - `PUT /tenants/{tenant_id}/data-retention` ← `{ "retention_days": "180" | null }`
  - `GET /tenant-portal/data-retention` → 同形（`GET /tenants/{id}/data-retention`と同じレスポンス形）
  - `PUT /tenant-portal/data-retention` ← 同形

- [ ] **Step 1: 失敗するテストを書く（PF管理者向けデフォルト設定）**

`core-api/tests/test_platform_billing_defaults.py`の末尾に追記:

```python
def test_get_data_retention_requires_platform_auth():
    resp = client.get("/platform/data-retention")
    assert resp.status_code == 401


def test_get_data_retention_returns_current_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_retention_days", return_value=365):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            "/platform/data-retention",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_retention_days": "365"}


def test_put_data_retention_updates_and_returns_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_retention_days") as mock_set, \
         patch("app.routers.platform.write_audit_log") as mock_audit:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/data-retention",
            json={"default_retention_days": "180"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_retention_days": "180"}
    mock_set.assert_called_once_with(mock_db, 180)
    assert mock_audit.called
    assert mock_db.commit.called


def test_put_data_retention_rejects_below_minimum():
    with patch("app.routers.platform.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/data-retention",
            json={"default_retention_days": "30"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_put_data_retention_rejects_invalid_value():
    with patch("app.routers.platform.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/data-retention",
            json={"default_retention_days": "not-a-number"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_platform_billing_defaults.py -k data_retention -v`
Expected: FAIL（404、対応するルートが無い）

- [ ] **Step 3: `platform.py`に実装を追加する**

`core-api/app/routers/platform.py`の以下のimport行:

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

を以下に置き換え:

```python
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_bill_shock_threshold,
    get_default_retention_days,
    get_default_unit_prices,
    get_tax_rate,
    set_default_bill_shock_threshold,
    set_default_retention_days,
    set_default_unit_prices,
    set_tax_rate,
    validate_bill_shock_threshold,
    validate_retention_days,
    validate_tax_rate,
    validate_unit_price,
    ITEM_KEYS,
)
```

`class BillShockThresholdItem(BaseModel):`の直後に追記:

```python
class DataRetentionItem(BaseModel):
    default_retention_days: str
```

ファイル末尾に追記:

```python


@router.get("/data-retention", response_model=DataRetentionItem)
def get_platform_data_retention(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        days = get_default_retention_days(db)
    return {"default_retention_days": str(days)}


@router.put("/data-retention", response_model=DataRetentionItem)
def update_platform_data_retention(body: DataRetentionItem, payload: dict = Depends(_require_platform)):
    try:
        days = validate_retention_days(body.default_retention_days)
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        set_default_retention_days(db, days)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "update_data_retention_default",
                        resource_type="billing_settings",
                        detail={"default_retention_days": days})
        db.commit()

    return {"default_retention_days": str(days)}
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_platform_billing_defaults.py -v`
Expected: PASS（全件）

- [ ] **Step 5: 失敗するテストを書く（テナント個別上書き、PF管理者向け）**

`core-api/tests/test_tenants.py`の末尾に追記:

```python
def test_get_tenant_data_retention_returns_override_when_set(client):
    tenant = MagicMock(data_retention_days=90)
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.get_effective_retention_days", return_value=90), \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/data-retention",
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90", "is_default": False}


def test_get_tenant_data_retention_tenant_not_found(client):
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/data-retention",
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 404


def test_put_tenant_data_retention_sets_override(client):
    tenant = MagicMock(data_retention_days=None)
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": "90"},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90"}
    assert tenant.data_retention_days == 90


def test_put_tenant_data_retention_clears_override_with_null(client):
    tenant = MagicMock(data_retention_days=90)
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": None},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": None}
    assert tenant.data_retention_days is None


def test_put_tenant_data_retention_rejects_below_minimum(client):
    tenant = MagicMock()
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": "10"},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 422
```

`core-api/tests/test_tenants.py`には`TENANT_ID`定数がまだ無いため、ファイル冒頭に追加する。以下の行:

```python
from unittest.mock import patch, MagicMock
import uuid
```

を以下に置き換え:

```python
from unittest.mock import patch, MagicMock
import uuid

TENANT_ID = "11111111-1111-1111-1111-111111111111"
```

（既存の`test_billing_api.py`と同じ値を使う。）

- [ ] **Step 6: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_tenants.py -k data_retention -v`
Expected: FAIL（404、対応するルートが無い）

- [ ] **Step 7: `tenants.py`に実装を追加する**

`core-api/app/routers/tenants.py`の以下のimport行:

```python
from app.services.tenant import setup_tenant, teardown_tenant, create_influxdb_bucket
from app.services.billing import seed_tenant_default_prices, get_default_retention_days
from app.config import settings
```

を以下に置き換え:

```python
from app.services.tenant import setup_tenant, teardown_tenant, create_influxdb_bucket
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_retention_days,
    get_effective_retention_days,
    seed_tenant_default_prices,
    validate_retention_days,
)
from app.config import settings
```

ファイル末尾に追記:

```python


class DataRetentionOut(BaseModel):
    retention_days: str
    is_default: bool


class DataRetentionSet(BaseModel):
    retention_days: str | None = None


@router.get("/{tenant_id}/data-retention", response_model=DataRetentionOut)
def get_tenant_data_retention(tenant_id: str, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        effective = get_effective_retention_days(db, tenant)
        is_default = tenant.data_retention_days is None
    return {"retention_days": str(effective), "is_default": is_default}


@router.put("/{tenant_id}/data-retention", response_model=DataRetentionSet)
def update_tenant_data_retention(tenant_id: str, body: DataRetentionSet, payload: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        if body.retention_days is None:
            tenant.data_retention_days = None
        else:
            try:
                tenant.data_retention_days = validate_retention_days(body.retention_days)
            except InvalidUnitPriceError as e:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "update_tenant_data_retention", tenant_id=tenant_id, resource_type="tenant",
                        detail={"retention_days": tenant.data_retention_days})
        db.commit()

    return {"retention_days": str(tenant.data_retention_days) if tenant.data_retention_days is not None else None}
```

（このエンドポイントは既存の`update_tenant`ルーターと同じ`tenants.py`に置く。billingとは無関係な一般テナント設定のため`billing.py`ルーターには置かない。）

- [ ] **Step 8: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_tenants.py -v`
Expected: PASS（全件）

- [ ] **Step 9: 失敗するテストを書く（テナント管理者セルフサービス）**

`core-api/tests/test_tenant_portal_data_retention.py`を新規作成:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_token(role: str = "admin"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def test_get_data_retention_requires_auth():
    resp = client.get("/tenant-portal/data-retention")
    assert resp.status_code == 401


def test_get_data_retention_rejects_non_admin():
    resp = client.get(
        "/tenant-portal/data-retention",
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403


def test_get_data_retention_allows_admin():
    tenant = MagicMock(data_retention_days=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.get_effective_retention_days", return_value=365):
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get(
            "/tenant-portal/data-retention",
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "365", "is_default": True}


def test_put_data_retention_sets_override_as_admin():
    tenant = MagicMock(data_retention_days=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/data-retention",
            json={"retention_days": "90"},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90"}
    assert tenant.data_retention_days == 90


def test_put_data_retention_rejects_non_admin():
    resp = client.put(
        "/tenant-portal/data-retention",
        json={"retention_days": "90"},
        cookies={"iot_token": _tenant_token("operator")},
    )
    assert resp.status_code == 403


def test_put_data_retention_rejects_below_minimum():
    tenant = MagicMock()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/data-retention",
            json={"retention_days": "10"},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 422
```

- [ ] **Step 10: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_data_retention.py -v`
Expected: FAIL（404、対応するルートが無い）

- [ ] **Step 11: `tenant_portal.py`に実装を追加する**

`core-api/app/routers/tenant_portal.py`の以下のimport行:

```python
from app.services.billing import get_invoice_detail_aggregated, list_invoices_aggregated
```

を以下に置き換え:

```python
from app.services.billing import (
    InvalidUnitPriceError,
    get_effective_retention_days,
    get_invoice_detail_aggregated,
    list_invoices_aggregated,
    validate_retention_days,
)
```

ファイル末尾（`get_my_invoice`関数の後）に追記:

```python


class DataRetentionOut(BaseModel):
    retention_days: str
    is_default: bool


class DataRetentionSet(BaseModel):
    retention_days: str | None = None


@router.get("/data-retention", response_model=DataRetentionOut)
def get_my_data_retention(payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        effective = get_effective_retention_days(db, tenant)
        is_default = tenant.data_retention_days is None
    return {"retention_days": str(effective), "is_default": is_default}


@router.put("/data-retention", response_model=DataRetentionSet)
def update_my_data_retention(body: DataRetentionSet, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()

        if body.retention_days is None:
            tenant.data_retention_days = None
        else:
            try:
                tenant.data_retention_days = validate_retention_days(body.retention_days)
            except InvalidUnitPriceError as e:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

        write_audit_log(db, "tenant", payload["sub"], payload["email"],
                        "update_data_retention", tenant_id=tenant_id, resource_type="tenant",
                        detail={"retention_days": tenant.data_retention_days})
        db.commit()

    return {"retention_days": str(tenant.data_retention_days) if tenant.data_retention_days is not None else None}
```

`BaseModel`が`tenant_portal.py`で既にインポートされていることを確認する（既に`from pydantic import BaseModel, Field, field_validator, model_validator`があるはずなので追加インポート不要）。

- [ ] **Step 12: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_data_retention.py -v`
Expected: PASS（全件）

- [ ] **Step 13: コミット**

```bash
git add core-api/app/routers/platform.py core-api/app/routers/tenants.py core-api/app/routers/tenant_portal.py core-api/tests/test_platform_billing_defaults.py core-api/tests/test_tenants.py core-api/tests/test_tenant_portal_data_retention.py
git commit -m "feat: データ保持期間のPF管理者・テナント管理者向けAPIを追加"
```

---

### Task 6: UI（PF管理者向けデフォルト設定・テナント個別上書き・テナント管理者セルフサービス）＋ドキュメント更新

**Files:**
- Modify: `admin-ui/js/api.js`
- Modify: `platform-ui/platform-settings.html`
- Modify: `platform-ui/tenant.html`
- Modify: `admin-ui/tenant-portal.html`
- Modify: `admin-ui/static/tailwind.css`（`npm run build:css`で再生成）
- Modify: `docs/superpowers/specs/2026-09-12-data-retention-design.md`（実装済みの旨を追記——このプランの完了をもって仕様書は完結扱いとし、追加の解決済みセクションは不要。Step 8で確認のみ行う）

このタスクにはTDDサイクル（テストなし・手動UI確認）を適用する。既存のUIには自動テストが無く、Alpine.js側は既存パターンの踏襲で足りるため、手動確認のみとする。

- [ ] **Step 1: `admin-ui/js/api.js`にAPI関数を追加する**

`admin-ui/js/api.js`の以下の行:

```javascript
        getBillingBillShockThreshold: () => request('GET', '/platform/billing/bill-shock-threshold'),
        updateBillingBillShockThreshold: (body) => request('PUT', '/platform/billing/bill-shock-threshold', body),
```

を以下に置き換え:

```javascript
        getBillingBillShockThreshold: () => request('GET', '/platform/billing/bill-shock-threshold'),
        updateBillingBillShockThreshold: (body) => request('PUT', '/platform/billing/bill-shock-threshold', body),
        getDataRetention: () => request('GET', '/platform/data-retention'),
        updateDataRetention: (body) => request('PUT', '/platform/data-retention', body),
```

- [ ] **Step 2: `platform-ui/platform-settings.html`にデフォルト保持期間カードを追加する**

`<script src="/admin/js/api.js?v=14">`を`<script src="/admin/js/api.js?v=15">`に変更する（Step 1でapi.jsに新規関数が増えたためキャッシュバスターを上げる）。

「ビルショック通知しきい値（デフォルト）」カードの`</div>`（`<p x-show="bsThresholdSaved" ...>しきい値を保存しました</p>`の直後、カードの閉じタグ）の後に、新しいカードを追加:

```html

    <div x-show="!loading" class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mt-6">
      <h2 class="text-base font-semibold text-gray-800 mb-1">データ保持期間（デフォルト）</h2>
      <p class="text-xs text-gray-400 mb-4">テレメトリデータをInfluxDBに保持する日数です。これより古いデータは自動的に削除されます（最短60日）。テナントごとに個別の上書き値を設定することもできます（テナント詳細画面、またはテナント管理者自身の設定）。</p>
      <div x-show="retentionError" class="mb-3 text-sm text-red-600" x-text="retentionError"></div>
      <div class="flex items-center justify-between py-2">
        <p class="text-sm font-medium text-gray-700">保持日数（日）</p>
        <input type="text" x-model="retentionDays" placeholder="例: 365"
               class="border border-gray-300 rounded px-2 py-1.5 text-sm w-32 text-right">
      </div>
      <button @click="saveRetentionDays()" :disabled="savingRetention || retentionLoadFailed"
              class="mt-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-4 py-2 rounded text-sm">
        <span x-show="!savingRetention">保存</span>
        <span x-show="savingRetention">保存中...</span>
      </button>
      <p x-show="retentionSaved" class="mt-3 text-sm text-green-600">保持期間を保存しました</p>
    </div>
```

`bsThresholdLoadFailed: false,`の行の直後に追記:

```javascript
        retentionDays: '',
        retentionError: '',
        retentionSaved: false,
        savingRetention: false,
        retentionLoadFailed: false,
```

`init()`メソッド内、ビルショックしきい値読み込みの`try { ... } catch(e) { this.bsThresholdError = ...; this.bsThresholdLoadFailed = true; }`ブロックの直後（`this.loading = false;`の前）に、新しい独立したtry/catchを追記:

```javascript
          try {
            const r = await api.platform.getDataRetention();
            this.retentionDays = r.default_retention_days;
          } catch(e) {
            this.retentionError = e.message;
            this.retentionLoadFailed = true;
          }
```

`saveBsThreshold()`メソッドの後に、新しいメソッドを追記:

```javascript
        async saveRetentionDays() {
          this.retentionSaved = false;
          this.retentionError = '';
          this.savingRetention = true;
          try {
            const result = await api.platform.updateDataRetention({ default_retention_days: String(this.retentionDays) });
            this.retentionDays = result.default_retention_days;
            this.retentionSaved = true;
            setTimeout(() => this.retentionSaved = false, 3000);
          } catch(e) { this.retentionError = e.message; }
          finally { this.savingRetention = false; }
        },
```

- [ ] **Step 3: `platform-ui/tenant.html`にテナント個別上書き入力欄を追加する**

`<script src="/admin/js/api.js?v=10">`はそのままでよい（このファイルは既存の`api.request`ジェネリック呼び出しパターンを使い、api.jsに新規関数を追加しない）。

ビルショックしきい値の個別上書きカード（`<div class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mt-4">`〜`</div>`、「ビルショック通知しきい値（このテナントの上書き）」のブロック）の直後に追加:

```html

        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mt-4">
          <p class="text-sm font-medium text-gray-700 mb-1">データ保持期間（このテナントの上書き）</p>
          <p class="text-xs text-gray-400 mb-3">
            現在の実効値: <span x-text="retentionEffective + ' 日'"></span>
            <span x-show="retentionIsDefault">（デフォルト値を使用中）</span>
            <span x-show="!retentionIsDefault">（このテナント専用の設定）</span>
            <br>空欄で保存するとデフォルト値に戻ります（最短60日）。
          </p>
          <div class="flex items-center gap-2">
            <input type="text" x-model="retentionDays" placeholder="未設定（デフォルトを使用）"
                   class="border border-gray-300 rounded px-2 py-1.5 text-sm w-40">
            <button @click="saveRetentionDays()" :disabled="savingRetention"
                    class="bg-blue-600 text-white text-sm px-3 py-1.5 rounded disabled:bg-blue-300">保存</button>
          </div>
          <p x-show="retentionSaved" class="mt-2 text-xs text-green-600">保存しました</p>
        </div>
```

`bsThreshold: '', bsThresholdIsDefault: true, bsEffective: '', savingBsThreshold: false, bsThresholdSaved: false,`の行の直後に追記:

```javascript
        retentionDays: '', retentionIsDefault: true, retentionEffective: '', savingRetention: false, retentionSaved: false,
```

`loadBillingPrices()`メソッド内の`this.loadBsThreshold();`の行の直後に追記:

```javascript
          this.loadRetentionDays();
```

`saveBsThreshold()`メソッドの後に、新しいメソッドを追記:

```javascript
        async loadRetentionDays() {
          try {
            const r = await api.request('GET', `/tenants/${tenantId}/data-retention`);
            this.retentionEffective = r.retention_days;
            this.retentionIsDefault = r.is_default;
            this.retentionDays = r.is_default ? '' : r.retention_days;
          } catch(e) { this.error = e.message; }
        },

        async saveRetentionDays() {
          this.retentionSaved = false;
          this.savingRetention = true;
          try {
            await api.request('PUT', `/tenants/${tenantId}/data-retention`, {
              retention_days: this.retentionDays === '' ? null : String(this.retentionDays),
            });
            await this.loadRetentionDays();
            this.retentionSaved = true;
            setTimeout(() => this.retentionSaved = false, 3000);
          } catch(e) { this.error = e.message; }
          finally { this.savingRetention = false; }
        },
```

- [ ] **Step 4: `admin-ui/tenant-portal.html`にテナント管理者セルフサービス欄を追加する**

「統計・ビリング」タブ（`<div x-show="activeTab === 'stats-billing'">`）の末尾、請求書一覧テーブルを囲む`</div>`の直後（このタブ全体を閉じる`</div>`の直前）に追加:

```html

          <div x-show="isAdmin" class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mt-4">
            <p class="text-sm font-medium text-gray-700 mb-1">データ保持期間</p>
            <p class="text-xs text-gray-400 mb-3">
              現在の実効値: <span x-text="retentionEffective + ' 日'"></span>
              <span x-show="retentionIsDefault">（PF管理者のデフォルト値を使用中）</span>
              <span x-show="!retentionIsDefault">（このテナント専用の設定）</span>
              <br>テレメトリデータをこの日数より古いものは自動削除します（最短60日）。空欄で保存するとデフォルトに戻ります。
            </p>
            <div class="flex items-center gap-2">
              <input type="text" x-model="retentionDays" placeholder="未設定（デフォルトを使用）"
                     class="border border-gray-300 rounded px-2 py-1.5 text-sm w-40">
              <button @click="saveRetentionDays()" :disabled="savingRetention"
                      class="bg-blue-600 text-white text-sm px-3 py-1.5 rounded disabled:bg-blue-300">保存</button>
            </div>
            <p x-show="retentionSaved" class="mt-2 text-xs text-green-600">保存しました</p>
          </div>
```

`invoices: [], invoicesLoading: false,`の行の直前に追記:

```javascript
    retentionDays: '', retentionIsDefault: true, retentionEffective: '', savingRetention: false, retentionSaved: false,
```

`loadStatsAndBilling()`メソッドを以下に置き換え（`loadRetention()`呼び出しを追加、`isAdmin`のときだけ呼ぶ——`_require_admin`が403を返すため非adminで呼んでもエラー表示になるだけだが、UIとしても無駄な失敗リクエストを避ける）:

```javascript
    async loadStatsAndBilling() {
      await this.loadStats();
      await this.loadInvoices();
      if (this.isAdmin) await this.loadRetention();
    },
    async loadRetention() {
      try {
        const r = await portalFetch('GET', '/data-retention');
        this.retentionEffective = r.retention_days;
        this.retentionIsDefault = r.is_default;
        this.retentionDays = r.is_default ? '' : r.retention_days;
      } catch(e) { this.error = e.message; }
    },
    async saveRetentionDays() {
      this.retentionSaved = false;
      this.savingRetention = true;
      try {
        await portalFetch('PUT', '/data-retention', {
          retention_days: this.retentionDays === '' ? null : String(this.retentionDays),
        });
        await this.loadRetention();
        this.retentionSaved = true;
        setTimeout(() => this.retentionSaved = false, 3000);
      } catch(e) { this.error = e.message; }
      finally { this.savingRetention = false; }
    },
```

（元の`loadStatsAndBilling()`定義:
```javascript
    async loadStatsAndBilling() {
      await this.loadStats();
      await this.loadInvoices();
    },
```
を上記の3メソッドのブロックで置き換える。）

- [ ] **Step 5: HTMLタグの整合性を確認する**

Run:
```bash
python3 -c "
import re
for path in ['platform-ui/platform-settings.html', 'platform-ui/tenant.html', 'admin-ui/tenant-portal.html']:
    content = open(path, encoding='utf-8').read()
    for tag in ['div','button','template']:
        opens = len(re.findall(r'<'+tag+r'[\s>]', content)) - len(re.findall(r'<'+tag+r'[^>]*/>', content))
        closes = len(re.findall(r'</'+tag+r'>', content))
        print(path, tag, opens, closes)
"
```
Expected: 各`tag`について`opens == closes`

- [ ] **Step 6: Tailwind CSSをビルドする**

Run: `npm run build:css`（リポジトリルートから実行）
Expected: `Done in ...ms.`（エラーなし）

- [ ] **Step 7: 手動UI確認**

開発環境でcore-apiを起動し、以下を確認する:
1. `/iotairx-console/platform-settings.html`で「データ保持期間（デフォルト）」カードが表示され、値を保存・再読込できること。60日未満を保存しようとするとエラーメッセージが表示されること。
2. `/iotairx-console/tenants.html`からテナント詳細を開き「単価設定」タブで、テナント個別の保持期間上書き欄が表示され、空欄保存で「デフォルトに戻る」こと。
3. テナントポータル（`admin-ui/tenant-portal.html`）に`role='admin'`でログインし、「統計・ビリング」タブに保持期間の自己設定欄が表示・保存できること。`role='operator'`や`'viewer'`ではこの欄が表示されないこと。
4. ブラウザのコンソールにJSエラーが出ていないこと。

（このステップは自動テストではなく手動確認。実施できない場合はその旨を明示的に報告する。）

- [ ] **Step 8: 設計仕様書の内容と実装の整合を確認する**

`docs/superpowers/specs/2026-09-12-data-retention-design.md`を読み直し、実装と食い違う記述が無いか確認する（あれば実装に合わせて修正する。今回は設計通りの実装なので変更不要のはずだが、念のため確認する）。

- [ ] **Step 9: コミット**

```bash
git add admin-ui/js/api.js platform-ui/platform-settings.html platform-ui/tenant.html admin-ui/tenant-portal.html admin-ui/static/tailwind.css
git commit -m "feat: データ保持期間設定のUIを追加"
```

---

### Task 7: 最終確認

**Files:** なし（既存ファイルの検証のみ）

- [ ] **Step 1: core-api全体のテストスイートを実行する**

Run: `cd core-api && python -m pytest tests/ -v`
Expected: 既存の無関係なベースライン失敗（`test_auth.py::test_hash_and_verify_password`等、bcryptバージョン起因の既知の7件）を除き、全件PASS。

- [ ] **Step 2: `docker compose config`で構文検証する**

Run: `docker compose config --quiet`
Expected: エラーなし

- [ ] **Step 3: 完了報告**

全タスクの完了をユーザーに報告し、pushしてよいか確認する。
