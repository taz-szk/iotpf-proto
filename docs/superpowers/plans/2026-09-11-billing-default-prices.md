# デフォルト料金テーブル管理機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PF管理者が新規テナント開通時の共通デフォルト単価を管理でき、テナント作成時にその値が自動的にそのテナントの単価としてセットされるようにする。

**Architecture:** テナント非依存のグローバル設定テーブル`billing_default_unit_prices`を新設し、`create_tenant()`内で新規テナントの`billing_unit_prices`へコピーする。PF管理者向けにGET/PUTのAPIと、既存の`platform-settings.html`に追加するUIセクションを用意する。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, PostgreSQL, Alpine.js, Tailwind CSS

**Spec:** docs/superpowers/specs/2026-09-11-billing-default-prices-design.md

## Global Constraints

- `billing_default_unit_prices`は`item_key`ごとに1行のみ保持する（履歴・`effective_from`は持たない、現在値のみ）
- テナント開通時のコピーは`set_unit_price()`の「変更は翌月からのみ適用」検証を経由しない（初期値の設定は「変更」ではない）
- 既存テナントへの遡及適用・「デフォルトにリセット」機能は今回実装しない
- 既存の`/tenants/{tenant_id}/billing/prices`（テナント別単価設定）の挙動は変更しない
- unit_priceのバリデーションルールは既存`create_price`（`app/routers/billing.py`）と同一: 10進数として解釈可能・有限・非負・最大値99999999.9999・小数点以下4桁まで

---

### Task 1: データモデル・マイグレーション

**Files:**
- Modify: `core-api/app/models/billing.py`
- Modify: `core-api/app/database.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_billing_default_prices_model.py`

**Interfaces:**
- Produces: `BillingDefaultUnitPrice`モデル（`app.models.billing`）、`migrate_create_billing_default_prices()`関数（`app.database`）。Task 2がモデルを使う。

- [ ] **Step 1: Write the failing test**

`core-api/tests/test_billing_default_prices_model.py`:

```python
from app.models.billing import BillingDefaultUnitPrice


def test_billing_default_unit_price_model_has_expected_columns():
    assert BillingDefaultUnitPrice.__tablename__ == "billing_default_unit_prices"
    columns = {c.name for c in BillingDefaultUnitPrice.__table__.columns}
    assert columns == {"item_key", "unit_price", "updated_at"}
    assert BillingDefaultUnitPrice.__table__.columns["item_key"].primary_key is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_billing_default_prices_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'BillingDefaultUnitPrice'`

- [ ] **Step 3: Write the implementation**

`core-api/app/models/billing.py`に追記（既存の`BillingLineItem`クラスの後）:

```python
class BillingDefaultUnitPrice(Base):
    __tablename__ = "billing_default_unit_prices"
    item_key = Column(String(50), primary_key=True)
    unit_price = Column(Numeric(12, 4), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

（`Column`/`String`/`Numeric`/`DateTime`/`func`は既存のimportにすでに含まれている）

`core-api/app/database.py`に新規マイグレーション関数を追加（既存の`migrate_create_billing_tables()`の直後）:

```python
def migrate_create_billing_default_prices() -> None:
    """billing_default_unit_prices テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_default_unit_prices (
                item_key    VARCHAR(50) PRIMARY KEY,
                unit_price  NUMERIC(12,4) NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.commit()
```

`core-api/app/main.py`の2箇所を編集する。

変更前:
```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables
```

変更後（`migrate_create_billing_default_prices`を末尾に追加）:
```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices
```

変更前:
```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables):
```

変更後:
```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_billing_default_prices_model.py -v`
Expected: PASS

Also verify the app still imports cleanly:
Run: `cd core-api && export POSTGRES_DSN="postgresql://test:test@localhost:5432/test" && python -c "from app.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add core-api/app/models/billing.py core-api/app/database.py core-api/app/main.py core-api/tests/test_billing_default_prices_model.py
git commit -m "feat(billing): デフォルト単価テーブルのモデル・マイグレーションを追加"
```

---

### Task 2: サービス層（取得・一括設定・テナント開通時のシード）

**Files:**
- Modify: `core-api/app/services/billing.py`
- Test: `core-api/tests/test_billing_default_prices.py`

**Interfaces:**
- Consumes: `BillingDefaultUnitPrice`（Task 1）、既存の`ITEM_KEYS`（同ファイル内）
- Produces:
  - `get_default_unit_prices(db) -> dict[str, Decimal]`
  - `set_default_unit_prices(db, prices: dict[str, Decimal]) -> None`
  - `seed_tenant_default_prices(db, tenant_id: str) -> None`
  - `InvalidUnitPriceError`（例外クラス）、`validate_unit_price(unit_price_str: str) -> Decimal`（既存`app/routers/billing.py`の`create_price`内バリデーションをここに抽出。Task 4がこれとルーター側の既存呼び出し箇所を両方使う）

- [ ] **Step 1: Write the failing tests**

`core-api/tests/test_billing_default_prices.py`:

```python
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_unit_prices,
    seed_tenant_default_prices,
    set_default_unit_prices,
    validate_unit_price,
)


def _default_row(item_key, unit_price):
    row = MagicMock()
    row.item_key = item_key
    row.unit_price = unit_price
    return row


def test_get_default_unit_prices_returns_dict():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = [
        _default_row("base_fee", Decimal("5000")),
        _default_row("data_points", Decimal("0.01")),
    ]
    result = get_default_unit_prices(mock_db)
    assert result == {"base_fee": Decimal("5000"), "data_points": Decimal("0.01")}


def test_get_default_unit_prices_empty_when_none_set():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = []
    assert get_default_unit_prices(mock_db) == {}


def test_set_default_unit_prices_replaces_all():
    mock_db = MagicMock()
    set_default_unit_prices(mock_db, {"base_fee": Decimal("5000"), "data_points": Decimal("0.01")})
    mock_db.query.return_value.delete.assert_called_once()
    assert mock_db.add.call_count == 2
    mock_db.commit.assert_called_once()


def test_seed_tenant_default_prices_copies_all_defaults():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = [
        _default_row("base_fee", Decimal("5000")),
        _default_row("data_points", Decimal("0.01")),
    ]
    seed_tenant_default_prices(mock_db, "tenant-1")
    assert mock_db.add.call_count == 2
    added = [call.args[0] for call in mock_db.add.call_args_list]
    assert all(a.tenant_id == "tenant-1" for a in added)
    assert all(a.effective_from == date.today().replace(day=1) for a in added)
    assert {a.item_key for a in added} == {"base_fee", "data_points"}


def test_seed_tenant_default_prices_noop_when_no_defaults():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = []
    seed_tenant_default_prices(mock_db, "tenant-1")
    mock_db.add.assert_not_called()


def test_validate_unit_price_accepts_valid_decimal():
    assert validate_unit_price("5000") == Decimal("5000")
    assert validate_unit_price("0.0100") == Decimal("0.0100")


def test_validate_unit_price_rejects_malformed_string():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("not-a-number")


def test_validate_unit_price_rejects_negative():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("-1")


def test_validate_unit_price_rejects_exceeding_maximum():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("100000000")


def test_validate_unit_price_rejects_too_many_decimal_places():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("1.00001")


def test_validate_unit_price_rejects_nan():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("NaN")


def test_validate_unit_price_rejects_infinity():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("Infinity")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && python -m pytest tests/test_billing_default_prices.py -v`
Expected: FAIL with `ImportError` (functions don't exist yet)

- [ ] **Step 3: Write the implementation**

`core-api/app/services/billing.py`の先頭のimportに`date`と`BillingDefaultUnitPrice`を追加:

変更前:
```python
from datetime import date
from decimal import Decimal, ROUND_DOWN

from sqlalchemy.orm import Session

from app.models.billing import BillingUnitPrice
```

変更後:
```python
from datetime import date
from decimal import Decimal, ROUND_DOWN, InvalidOperation

from sqlalchemy.orm import Session

from app.models.billing import BillingDefaultUnitPrice, BillingUnitPrice
```

ファイル末尾に追記:

```python
class InvalidUnitPriceError(Exception):
    pass


def validate_unit_price(unit_price_str: str) -> Decimal:
    """unit_priceの文字列をDecimalに変換し、課金単価として妥当かを検証する。
    不正な場合はInvalidUnitPriceError（メッセージはHTTPレスポンスのdetailに使う想定）を投げる。"""
    try:
        unit_price = Decimal(unit_price_str)
    except InvalidOperation:
        raise InvalidUnitPriceError("unit_price must be a decimal number")
    if not unit_price.is_finite():
        raise InvalidUnitPriceError("unit_price must be a finite decimal number")
    if unit_price < 0:
        raise InvalidUnitPriceError("unit_price must not be negative")
    if unit_price > Decimal("99999999.9999"):
        raise InvalidUnitPriceError("unit_price exceeds the maximum (99999999.9999)")
    if unit_price.as_tuple().exponent < -4:
        raise InvalidUnitPriceError("unit_price supports at most 4 decimal places")
    return unit_price


def get_default_unit_prices(db: Session) -> dict[str, Decimal]:
    """設定済みのデフォルト単価を item_key -> unit_price の辞書で返す。"""
    rows = db.query(BillingDefaultUnitPrice).all()
    return {row.item_key: Decimal(str(row.unit_price)) for row in rows}


def set_default_unit_prices(db: Session, prices: dict[str, Decimal]) -> None:
    """デフォルト単価を全件置き換えする（既存を全削除してprices全件を新規挿入）。"""
    db.query(BillingDefaultUnitPrice).delete()
    for item_key, unit_price in prices.items():
        db.add(BillingDefaultUnitPrice(item_key=item_key, unit_price=unit_price))
    db.commit()


def seed_tenant_default_prices(db: Session, tenant_id: str) -> None:
    """テナント開通時にデフォルト単価をそのテナントの単価として設定する。
    set_unit_price()の「変更は翌月からのみ適用」ルールは適用しない
    （これは初期値の設定であり「変更」ではないため）。デフォルトが1件も無ければ何もしない。
    呼び出し側のdb.commit()と同じトランザクションに含めること（このテナントのINSERTは
    ここではcommitしない）。"""
    defaults = db.query(BillingDefaultUnitPrice).all()
    month_start = date.today().replace(day=1)
    for row in defaults:
        db.add(BillingUnitPrice(
            tenant_id=tenant_id, item_key=row.item_key,
            unit_price=row.unit_price, effective_from=month_start,
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && python -m pytest tests/test_billing_default_prices.py -v`
Expected: PASS（13件）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/billing.py core-api/tests/test_billing_default_prices.py
git commit -m "feat(billing): デフォルト単価の取得・一括設定・テナント開通時シード関数を実装"
```

---

### Task 3: テナント開通フローへの組み込み

**Files:**
- Modify: `core-api/app/routers/tenants.py`
- Test: `core-api/tests/test_tenants.py`

**Interfaces:**
- Consumes: `seed_tenant_default_prices(db, tenant_id: str) -> None`（Task 2）

- [ ] **Step 1: Write the failing test**

`core-api/tests/test_tenants.py`に追記（既存の`test_create_tenant_success`と同じファイル）:

```python
def test_create_tenant_seeds_default_prices(client):
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
         patch("app.routers.tenants.seed_tenant_default_prices") as mock_seed, \
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
    mock_seed.assert_called_once_with(mock_db, tenant_id)
```

（このファイルは`client`をmodule-levelでは定義せず、`conftest.py`の`client`フィクスチャを関数引数で受け取る既存パターンを使っている。`test_create_tenant_success`と同じ`headers={"Authorization": "Bearer dummy"}`をそのまま使う——`verify_token`自体をパッチしているので実際のトークン文字列の値は検証されない）

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenants.py::test_create_tenant_seeds_default_prices -v`
Expected: FAIL — `mock_seed.assert_called_once_with(...)` fails because `seed_tenant_default_prices` is never called (AttributeError on patch target if not yet imported, or AssertionError)

- [ ] **Step 3: Write the implementation**

`core-api/app/routers/tenants.py`のimportに追加:

変更前:
```python
from app.services.tenant import setup_tenant, teardown_tenant
```

変更後:
```python
from app.services.tenant import setup_tenant, teardown_tenant
from app.services.billing import seed_tenant_default_prices
```

`create_tenant()`内、`db.flush()`の後・`db.commit()`の前に1行追加する。

変更前:
```python
        tenant = Tenant(id=uuid.uuid4(), name=body.name, slug=body.slug)
        db.add(tenant)
        db.flush()
        tenant_id = str(tenant.id)
        org_id, token, grafana_org_id = setup_tenant(tenant_id, body.name)
        tenant.influxdb_org_id = org_id
        tenant.influxdb_token = token
        tenant.grafana_org_id = str(grafana_org_id)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "create_tenant", tenant_id=tenant_id, resource_type="tenant", resource_id=body.name)
        db.commit()
```

変更後:
```python
        tenant = Tenant(id=uuid.uuid4(), name=body.name, slug=body.slug)
        db.add(tenant)
        db.flush()
        tenant_id = str(tenant.id)
        org_id, token, grafana_org_id = setup_tenant(tenant_id, body.name)
        tenant.influxdb_org_id = org_id
        tenant.influxdb_token = token
        tenant.grafana_org_id = str(grafana_org_id)
        seed_tenant_default_prices(db, tenant_id)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "create_tenant", tenant_id=tenant_id, resource_type="tenant", resource_id=body.name)
        db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenants.py -v`
Expected: PASS（既存分含め全件。新規テストも含む）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/routers/tenants.py core-api/tests/test_tenants.py
git commit -m "feat(billing): テナント開通時にデフォルト単価を自動セットするよう組み込み"
```

---

### Task 4: 単価バリデーション共通化 + PF管理者向けAPI

**Files:**
- Modify: `core-api/app/routers/billing.py`
- Test: `core-api/tests/test_billing_api.py`（既存ファイルに追記）、`core-api/tests/test_platform_billing_defaults.py`（新規）

**Interfaces:**
- Consumes: `validate_unit_price`/`InvalidUnitPriceError`/`get_default_unit_prices`/`set_default_unit_prices`（Task 2）
- Produces: `GET /platform/billing/default-prices` → `list[UnitPriceOut]`風の`[{"item_key": str, "unit_price": str}]`、`PUT /platform/billing/default-prices` → 同形式で更新後の一覧を返す

- [ ] **Step 1: Write the failing tests**

まず既存の`core-api/app/routers/billing.py`の`create_price`を、新しい共通バリデータを使うようリファクタする——これはTask 2で追加した`validate_unit_price`/`InvalidUnitPriceError`が実際に動くことを既存テストで確認するためのステップ。既存の`core-api/tests/test_billing_api.py`は変更不要（エラーメッセージ文字列は変えないので、既存のstatus_codeベースのアサーションはそのまま通る）。

新規ファイル `core-api/tests/test_platform_billing_defaults.py`:

```python
from decimal import Decimal
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token():
    return create_access_token({"sub": "user-id", "email": "user@test.com", "type": "tenant",
                                 "tenant_id": "11111111-1111-1111-1111-111111111111", "role": "admin"})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_get_default_prices_requires_platform_auth():
    resp = client.get("/platform/billing/default-prices")
    assert resp.status_code == 401


def test_get_default_prices_rejects_tenant_token():
    resp = client.get("/platform/billing/default-prices",
                       headers={"Authorization": f"Bearer {_tenant_token()}"})
    assert resp.status_code == 401


def test_get_default_prices_returns_current_values():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_unit_prices",
               return_value={"base_fee": Decimal("5000"), "data_points": Decimal("0.01")}):
        mock_session.return_value = _session_ctx()
        resp = client.get("/platform/billing/default-prices",
                           headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    body = resp.json()
    by_key = {item["item_key"]: item["unit_price"] for item in body}
    assert by_key == {"base_fee": "5000", "data_points": "0.01"}


def test_put_default_prices_replaces_and_returns_updated_list():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_unit_prices") as mock_set:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/billing/default-prices",
            json=[{"item_key": "base_fee", "unit_price": "5000"}],
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    mock_set.assert_called_once()
    called_prices = mock_set.call_args[0][1]
    assert called_prices == {"base_fee": Decimal("5000")}
    body = resp.json()
    assert body == [{"item_key": "base_fee", "unit_price": "5000"}]


def test_put_default_prices_rejects_invalid_unit_price():
    resp = client.put(
        "/platform/billing/default-prices",
        json=[{"item_key": "base_fee", "unit_price": "not-a-number"}],
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_put_default_prices_rejects_unknown_item_key():
    resp = client.put(
        "/platform/billing/default-prices",
        json=[{"item_key": "not_a_real_item", "unit_price": "5000"}],
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && python -m pytest tests/test_platform_billing_defaults.py -v`
Expected: FAIL (404 — routes don't exist yet)

- [ ] **Step 3: Write the implementation**

`core-api/app/routers/billing.py`の`create_price`関数のバリデーション部分をリファクタする。

変更前:
```python
@router.post("/prices", response_model=UnitPriceOut, status_code=status.HTTP_201_CREATED)
def create_price(tenant_id: str, body: UnitPriceSet, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    try:
        unit_price = Decimal(body.unit_price)
    except InvalidOperation:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price must be a decimal number")
    if not unit_price.is_finite():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price must be a finite decimal number")
    if unit_price < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price must not be negative")
    if unit_price > Decimal("99999999.9999"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price exceeds the maximum (99999999.9999)")
    if unit_price.as_tuple().exponent < -4:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price supports at most 4 decimal places")
```

変更後:
```python
@router.post("/prices", response_model=UnitPriceOut, status_code=status.HTTP_201_CREATED)
def create_price(tenant_id: str, body: UnitPriceSet, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    try:
        unit_price = validate_unit_price(body.unit_price)
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
```

対応するimportの変更。変更前:
```python
from app.services.billing import (
    InvalidEffectiveDateError,
    get_effective_unit_prices,
    set_unit_price,
)
```

変更後:
```python
from app.services.billing import (
    InvalidEffectiveDateError,
    InvalidUnitPriceError,
    get_effective_unit_prices,
    set_unit_price,
    validate_unit_price,
)
```

`Decimal`/`InvalidOperation`のimportがファイル冒頭にまだ残っている場合は消さない（他の箇所——`unit_price > Decimal(...)`のような比較——で使っていないか確認し、使っていなければ`InvalidOperation`のみ不要になるので、実際に`grep`で使用箇所を確認してから消すかどうか判断すること。安全側に倒すなら残しておいてもエラーにはならない）。

新規に`core-api/app/routers/platform.py`へ追記:

```python
from decimal import Decimal
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_unit_prices,
    set_default_unit_prices,
    validate_unit_price,
)
from app.services.billing import ITEM_KEYS


class DefaultPriceItem(BaseModel):
    item_key: str
    unit_price: str


@router.get("/billing/default-prices", response_model=list[DefaultPriceItem])
def get_billing_default_prices(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        prices = get_default_unit_prices(db)
    return [{"item_key": k, "unit_price": str(v)} for k, v in prices.items()]


@router.put("/billing/default-prices", response_model=list[DefaultPriceItem])
def update_billing_default_prices(body: list[DefaultPriceItem], payload: dict = Depends(_require_platform)):
    validated: dict[str, Decimal] = {}
    for item in body:
        if item.item_key not in ITEM_KEYS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                 detail=f"Unknown item_key: {item.item_key}")
        try:
            validated[item.item_key] = validate_unit_price(item.unit_price)
        except InvalidUnitPriceError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        set_default_unit_prices(db, validated)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "update_billing_default_prices",
                        resource_type="billing_default_unit_prices",
                        detail={k: str(v) for k, v in validated.items()})

    return [{"item_key": k, "unit_price": str(v)} for k, v in validated.items()]
```

（`write_audit_log`は既に`app/routers/platform.py`にimportされている。`BaseModel`も既にimportされている。`status`も既にimportされている）

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && python -m pytest tests/test_platform_billing_defaults.py tests/test_billing_api.py -v`
Expected: PASS（新規6件 + 既存分すべて）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/routers/billing.py core-api/app/routers/platform.py core-api/tests/test_platform_billing_defaults.py
git commit -m "feat(billing): デフォルト単価のPF管理者向けAPI(GET/PUT)を追加し、既存バリデーションを共通化"
```

---

### Task 5: PF管理者UI

**Files:**
- Modify: `platform-ui/platform-settings.html`
- Modify: `admin-ui/js/api.js`

**Interfaces:**
- Consumes: `GET /platform/billing/default-prices`、`PUT /platform/billing/default-prices`（Task 4）

このタスクはUIのみでバックエンドのユニットテストは対象外。手動確認手順をStepとして記載する。

- [ ] **Step 1: api.jsに新しいAPI呼び出しを追加**

`admin-ui/js/api.js`の`platform`セクションに追記。

変更前:
```javascript
    platform: {
        getMfaSettings: () => request('GET',   '/platform/mfa-settings'),
        updateMfaSettings: (body) => request('PATCH', '/platform/mfa-settings', body),
        grafanaOrgId: () => request('GET', '/tenants/platform/grafana-org-id'),
    },
```

変更後:
```javascript
    platform: {
        getMfaSettings: () => request('GET',   '/platform/mfa-settings'),
        updateMfaSettings: (body) => request('PATCH', '/platform/mfa-settings', body),
        grafanaOrgId: () => request('GET', '/tenants/platform/grafana-org-id'),
        getBillingDefaultPrices: () => request('GET', '/platform/billing/default-prices'),
        updateBillingDefaultPrices: (body) => request('PUT', '/platform/billing/default-prices', body),
    },
```

- [ ] **Step 2: platform-settings.htmlに新しいセクションを追加**

MFA設定セクションの`<div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6">...</div>`の閉じタグの直後、`</div>`（`x-data`のラッパーdiv）が閉じる前に追加する。

変更前:
```html
      <p x-show="saved" class="mt-4 text-sm text-green-600">設定を保存しました</p>
    </div>
  </div>
```

変更後:
```html
      <p x-show="saved" class="mt-4 text-sm text-green-600">設定を保存しました</p>
    </div>

    <div x-show="!loading" class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mt-6">
      <h2 class="text-base font-semibold text-gray-800 mb-1">デフォルト料金テーブル</h2>
      <p class="text-xs text-gray-400 mb-4">新規テナント開通時に、ここで設定した単価がそのテナントの初期単価として自動セットされます（開通後の既存テナントには影響しません）</p>
      <div x-show="defaultPricesError" class="mb-3 text-sm text-red-600" x-text="defaultPricesError"></div>
      <div class="space-y-3">
        <template x-for="item in billingItems" :key="item.key">
          <div class="flex items-center justify-between py-2 border-b border-gray-100">
            <div>
              <p class="text-sm font-medium text-gray-700" x-text="item.label"></p>
              <p class="text-xs text-gray-400" x-text="item.unit"></p>
            </div>
            <input type="text" x-model="defaultPrices[item.key]" placeholder="未設定（0円）"
                   class="border border-gray-300 rounded px-2 py-1.5 text-sm w-32 text-right">
          </div>
        </template>
      </div>
      <button @click="saveDefaultPrices()" :disabled="savingDefaultPrices"
              class="mt-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-4 py-2 rounded text-sm">
        <span x-show="!savingDefaultPrices">保存</span>
        <span x-show="savingDefaultPrices">保存中...</span>
      </button>
      <p x-show="defaultPricesSaved" class="mt-3 text-sm text-green-600">デフォルト料金テーブルを保存しました</p>
    </div>
  </div>
```

`<script>`内の`settingsApp()`を編集する。

変更前:
```javascript
    function settingsApp() {
      return {
        loading: true, error: '', saved: false,
        settings: { platform_required: false, tenant_required: false },
        async init() {
          try {
            this.settings = await api.platform.getMfaSettings();
          } catch(e) { this.error = e.message; }
          finally { this.loading = false; }
        },
        async toggle(key) {
          this.saved = false;
          this.error = '';
          this.settings[key] = !this.settings[key];
          try {
            await api.platform.updateMfaSettings({ [key]: this.settings[key] });
            this.saved = true;
            setTimeout(() => this.saved = false, 3000);
          } catch(e) { this.error = e.message; this.settings[key] = !this.settings[key]; }
        },
      };
    }
```

変更後:
```javascript
    function settingsApp() {
      return {
        loading: true, error: '', saved: false,
        settings: { platform_required: false, tenant_required: false },
        billingItems: [
          { key: 'base_fee', label: '基本料金', unit: '月額' },
          { key: 'data_points', label: 'データポイント数', unit: '件あたり' },
          { key: 'device_count', label: 'デバイス数（月内通信）', unit: '台あたり' },
          { key: 'provisionable_devices', label: 'プロビジョニング可能数', unit: '台あたり' },
          { key: 'alert_events', label: 'アラートイベント数', unit: '件あたり' },
        ],
        defaultPrices: {},
        defaultPricesError: '',
        defaultPricesSaved: false,
        savingDefaultPrices: false,
        async init() {
          try {
            this.settings = await api.platform.getMfaSettings();
            const rows = await api.platform.getBillingDefaultPrices();
            for (const row of rows) this.defaultPrices[row.item_key] = row.unit_price;
          } catch(e) { this.error = e.message; }
          finally { this.loading = false; }
        },
        async toggle(key) {
          this.saved = false;
          this.error = '';
          this.settings[key] = !this.settings[key];
          try {
            await api.platform.updateMfaSettings({ [key]: this.settings[key] });
            this.saved = true;
            setTimeout(() => this.saved = false, 3000);
          } catch(e) { this.error = e.message; this.settings[key] = !this.settings[key]; }
        },
        async saveDefaultPrices() {
          this.defaultPricesSaved = false;
          this.defaultPricesError = '';
          this.savingDefaultPrices = true;
          const body = this.billingItems
            .filter(item => this.defaultPrices[item.key] !== undefined && this.defaultPrices[item.key] !== '')
            .map(item => ({ item_key: item.key, unit_price: String(this.defaultPrices[item.key]) }));
          try {
            await api.platform.updateBillingDefaultPrices(body);
            this.defaultPricesSaved = true;
            setTimeout(() => this.defaultPricesSaved = false, 3000);
          } catch(e) { this.defaultPricesError = e.message; }
          finally { this.savingDefaultPrices = false; }
        },
      };
    }
```

- [ ] **Step 3: 手動確認**

1. core-apiとplatform-uiを起動する（既存の開発環境起動手順に従う）
2. PF管理者でログインし、「プラットフォーム設定」画面を開く
3. 「デフォルト料金テーブル」セクションが表示され、未設定項目は空欄（プレースホルダ「未設定（0円）」表示）であることを確認する
4. いくつかの項目に単価を入力して「保存」をクリックし、成功メッセージが出ることを確認する
5. 画面を再読み込みし、保存した値がそのまま復元されることを確認する
6. PF管理者画面から新規テナントを作成し、そのテナントの「単価設定」タブ（`platform-ui/tenant.html`の請求タブ）を開いて、手順4で設定したデフォルト単価が当月分としてすでに入っていることを確認する

- [ ] **Step 4: Commit**

```bash
git add platform-ui/platform-settings.html admin-ui/js/api.js
git commit -m "feat(billing): PF管理者画面にデフォルト料金テーブル設定セクションを追加"
```

---

## 完了後の確認（フルテストスイート）

```bash
cd core-api
export POSTGRES_DSN="postgresql://test:test@localhost:5432/test"
python -m pytest tests/test_billing_default_prices_model.py tests/test_billing_default_prices.py tests/test_tenants.py tests/test_platform_billing_defaults.py tests/test_billing_api.py -v
```

Expected: 全件PASS。
