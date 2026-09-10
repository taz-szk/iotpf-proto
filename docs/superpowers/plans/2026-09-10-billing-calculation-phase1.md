# 料金積算機能 Phase 1（単価マスタ + 積算エンジン）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナントごとの課金単価マスタ（基本料金・従量単価）を管理でき、利用量から明細行・消費税・合計金額を仕様書通りのルール（端数切り捨て、単価は翌月から適用）で計算できる状態にする。

**Architecture:** `core-api`（FastAPI）に新規ドメイン `billing` を追加する。単価マスタは `public` スキーマの新規テーブル（Tenant への FK 付き）に SQLAlchemy ORM で保存する。積算ロジックは DB / 外部サービスに依存しない純粋関数として実装し、単体テストしやすくする。既存の `device_groups`（router/service/schema の3層構成）と `dashboard_panel_configs`（public スキーマ ORM モデル + `migrate_*` 関数）のパターンをそのまま踏襲する。

**Tech Stack:** FastAPI, SQLAlchemy 2.x ORM, PostgreSQL（`public` スキーマ、`CREATE TABLE IF NOT EXISTS` による冪等マイグレーション）, pytest + unittest.mock。

**Spec:** [docs/superpowers/specs/2026-09-10-billing-calculation-design.md](../specs/2026-09-10-billing-calculation-design.md)

## Global Constraints

- 明細行の金額 = `quantity × unit_price` を **1円未満切り捨て**（行ごとに切り捨ててから合計する。合計してから丸めない）（仕様書 4.1節）
- 消費税額 = 明細合計 × 10% を **1円未満切り捨て**（仕様書 4.1節、税率はいったんハードコード。税率マスタ化は次回検討＝仕様書5節、今回スコープ外）
- 単価の適用開始日（`effective_from`）は **常に月初日**で、**当月以前の日付は設定不可**（変更は必ず翌月以降から適用。日割りなし）（仕様書 2.4節）
- 無制限プロビジョニングトークンは課金対象から除外し、既存の `provisionable_devices` フィールド（有限トークンの残り容量合計）をそのまま利用量として使う（仕様書 2.2節）— Phase 2 で実装
- 課金項目キーは次の5種で固定: `base_fee`, `data_points`, `device_count`, `provisionable_devices`, `alert_events`（仕様書 1節の表）

## スコープ決定（このPhaseで確定させたこと）

仕様書は「単価マスタ設定」から「バッチ確定・請求ステータス管理」まで広い範囲をカバーしている。1つのPlanに収めるには大きすぎるため、`writing-plans` スキルの Scope Check に従い2フェーズに分割する。

**Phase 1（このPlan）:** 単価マスタ（テナントごとのCRUD）+ 積算エンジン（利用量→明細行→合計金額の純粋計算ロジック）。DBに保存された単価と、渡された利用量から金額を計算できることをテストで保証するところまで。

**Phase 2（別Plan・今回スコープ外）:** InfluxDB/PostgreSQLからの月次利用量集計（当月データポイント数・当月ユニークデバイス数・アラートイベント数の月指定版クエリ）、`billing_invoices`/`billing_line_items` への実際の請求書生成（draft作成）、月次バッチ実行、`finalized`/`corrected` ステータス遷移と訂正差分、ビルショック閾値通知、単価管理UI。理由: これらはPostgreSQL/InfluxDBの実データに依存し、Phase 1の単価マスタ・積算エンジンが先に安定して存在しないと設計できないため。

**契約プラン単位の単価（仕様書3節「テナントごと、または契約プランごと」）は今回スコープ外・テナントごとのみ実装する。** 理由: 現在のコードベースに「契約プラン」という概念自体が存在しない（`Tenant` モデルにプランを表すカラムがない）。プラン単位の一括設定はYAGNI — 必要になった時点でテナントごとの単価設定の上に追加できる。

**税率はハードコード（10%）とする。** 仕様書5節で「税率マスタ化」は次回検討事項として未確定のまま残されているため、決め打ちせずコード内の定数として実装し、将来の税率マスタ追加時に差し替えやすい形（`calculate_invoice` の `tax_rate` 引数）にしておく。

---

### Task 1: 前提修正 — テナントポータルの無制限トークン発行を禁止する

仕様書2.2節・6節で明記されている必須の前提修正。テナント管理者が `max_devices: null` を送ると無制限トークンが発行できてしまう抜け穴を塞ぐ（無制限トークンはPF管理者専用の隠し仕様とする決定のため）。

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py:374-375`（`TokenCreate.max_devices` の型を `Optional[int]` → `int` に変更）
- Modify: `core-api/app/routers/tenant_portal.py:417`（`None` 分岐が不要になるため単純化）
- Test: `core-api/tests/test_tenant_portal_tokens.py`（新規）

**Interfaces:**
- Consumes: なし（既存コードの修正のみ）
- Produces: なし（Phase 1 の他タスクはこの変更に依存しない）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_tenant_portal_tokens.py` を新規作成する。

```python
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_jwt(role: str = "admin"):
    return create_access_token({
        "sub": "user-id",
        "email": "user@acme.com",
        "type": "tenant",
        "role": role,
        "tenant_id": _TENANT_ID,
    })


def test_create_token_rejects_null_max_devices():
    resp = client.post(
        "/tenant-portal/me/tokens",
        json={"max_devices": None},
        cookies={"iot_token": _tenant_jwt()},
    )
    assert resp.status_code == 422


def test_create_token_rejects_zero_max_devices():
    resp = client.post(
        "/tenant-portal/me/tokens",
        json={"max_devices": 0},
        cookies={"iot_token": _tenant_jwt()},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run（`core-api/` ディレクトリで）: `python -m pytest tests/test_tenant_portal_tokens.py -v`
Expected: `test_create_token_rejects_null_max_devices` が FAIL する（現状は `max_devices: null` が受理され 201 が返る、またはDB接続エラーで別の失敗をする）。`test_create_token_rejects_zero_max_devices` は既存の `gt=0` 制約により既にPASSしているはず。

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/routers/tenant_portal.py` の該当箇所を編集する。

変更前（374-375行目付近）:
```python
class TokenCreate(BaseModel):
    max_devices: Optional[int] = Field(default=100, gt=0, le=10000)
    expires_days: Optional[int] = Field(default=365, gt=0, le=1825)
```

変更後:
```python
class TokenCreate(BaseModel):
    max_devices: int = Field(default=100, gt=0, le=10000)
    expires_days: Optional[int] = Field(default=365, gt=0, le=1825)
```

変更前（417行目付近、`create_token` 関数内）:
```python
            max_devices=_UNLIMITED_DEVICES if body.max_devices is None else body.max_devices,
```

変更後:
```python
            max_devices=body.max_devices,
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_tenant_portal_tokens.py -v`
Expected: PASS（2件とも）

- [ ] **Step 5: 既存のテナントポータル関連テストにリグレッションがないことを確認する**

Run: `python -m pytest tests/test_tenant_portal_stats.py tests/test_tenant_portal_public_access.py -v`
Expected: 全てPASS

- [ ] **Step 6: コミット**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_tokens.py
git commit -m "fix(billing-prep): テナントポータルでの無制限プロビジョニングトークン発行を禁止"
```

---

### Task 2: billing用DBテーブルのマイグレーション関数を追加する

`billing_unit_prices`（単価マスタ）・`billing_invoices`（請求書ヘッダ、Phase 2で使用）・`billing_line_items`（明細行、Phase 2で使用）の3テーブルを作成する。Phase 1では `billing_unit_prices` のみ実際に使うが、Phase 2の手戻りを避けるため仕様書4.3節のテーブル設計に従い3テーブルまとめて作成する（DDLをこの1タスクにまとめておいた方が、後から ALTER で追加するより簡単なため）。

**Files:**
- Modify: `core-api/app/database.py`（`migrate_create_billing_tables()` 関数を追加、既存の `migrate_create_audit_logs()` の直後に配置）
- Modify: `core-api/app/main.py:6,27`（import と migrate ループへの登録）
- Test: `core-api/tests/test_db.py`（既存ファイルにテストを追加）

**Interfaces:**
- Consumes: なし
- Produces: `billing_unit_prices(id, tenant_id, item_key, unit_price, effective_from, created_at)`、`billing_invoices(id, tenant_id, target_year_month, status, subtotal, tax_amount, total_amount, created_at, finalized_at)`、`billing_line_items(id, invoice_id, item_key, quantity, unit_price, amount, created_at)` — Task 3（ORMモデル）がこのテーブル定義に対応するカラム名・型を使う

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_db.py` の末尾に追加する。

```python
def test_migrate_create_billing_tables_executes():
    from app.database import migrate_create_billing_tables
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        migrate_create_billing_tables()
    sql_calls = _sql_text(mock_conn.execute.call_args_list)
    assert "billing_unit_prices" in sql_calls
    assert "billing_invoices" in sql_calls
    assert "billing_line_items" in sql_calls
    assert "UNIQUE (tenant_id, item_key, effective_from)" in sql_calls
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_db.py::test_migrate_create_billing_tables_executes -v`
Expected: FAIL（`ImportError: cannot import name 'migrate_create_billing_tables'`）

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/database.py` の `migrate_create_audit_logs()` 関数の直後（371行目付近）に追加する。

```python
def migrate_create_billing_tables() -> None:
    """billing_unit_prices・billing_invoices・billing_line_items テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_unit_prices (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                item_key       VARCHAR(50) NOT NULL,
                unit_price     NUMERIC(12,4) NOT NULL,
                effective_from DATE NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, item_key, effective_from)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_invoices (
                id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                target_year_month  VARCHAR(7) NOT NULL,
                status             VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'finalized', 'corrected')),
                subtotal           INTEGER NOT NULL DEFAULT 0,
                tax_amount         INTEGER NOT NULL DEFAULT 0,
                total_amount       INTEGER NOT NULL DEFAULT 0,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                finalized_at       TIMESTAMPTZ
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_line_items (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                invoice_id  UUID NOT NULL REFERENCES billing_invoices(id) ON DELETE CASCADE,
                item_key    VARCHAR(50) NOT NULL,
                quantity    INTEGER NOT NULL,
                unit_price  NUMERIC(12,4) NOT NULL,
                amount      INTEGER NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_billing_unit_prices_tenant_item
                ON billing_unit_prices(tenant_id, item_key, effective_from DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant_month
                ON billing_invoices(tenant_id, target_year_month)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_billing_line_items_invoice
                ON billing_line_items(invoice_id)
        """))
        conn.commit()
```

`core-api/app/main.py` の6行目のimport文に `migrate_create_billing_tables` を追加し、27行目のタプルにも追加する。

変更前（6行目）:
```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id
```

変更後:
```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables
```

変更前（27行目）:
```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id):
```

変更後:
```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables):
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_db.py -v`
Expected: 全てPASS

- [ ] **Step 5: コミット**

```bash
git add core-api/app/database.py core-api/app/main.py core-api/tests/test_db.py
git commit -m "feat(billing): 単価マスタ・請求書・明細行テーブルのマイグレーションを追加"
```

---

### Task 3: billingのSQLAlchemyモデルを追加する

**Files:**
- Create: `core-api/app/models/billing.py`
- Modify: `core-api/app/main.py`（起動時に `Base.metadata` へモデルを登録させるため、モデルモジュールがどこかでimportされる必要がある。Task 4以降のサービス層が `app.models.billing` を import するので、実行時には自動的にロードされる。このタスク単体では追加のimportは不要）

**Interfaces:**
- Consumes: Task 2 で作成したテーブル定義（カラム名・型が一致している必要がある）
- Produces: `BillingUnitPrice(id, tenant_id, item_key, unit_price, effective_from, created_at)`、`BillingInvoice(id, tenant_id, target_year_month, status, subtotal, tax_amount, total_amount, created_at, finalized_at)`、`BillingLineItem(id, invoice_id, item_key, quantity, unit_price, amount, created_at)` — Task 5（サービス層）が `BillingUnitPrice` を使う

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_billing_models.py` を新規作成する。

```python
from decimal import Decimal
from datetime import date
from app.models.billing import BillingUnitPrice, BillingInvoice, BillingLineItem


def test_billing_unit_price_table_name_and_columns():
    assert BillingUnitPrice.__tablename__ == "billing_unit_prices"
    row = BillingUnitPrice(
        tenant_id="11111111-1111-1111-1111-111111111111",
        item_key="base_fee",
        unit_price=Decimal("5000"),
        effective_from=date(2026, 10, 1),
    )
    assert row.item_key == "base_fee"
    assert row.unit_price == Decimal("5000")


def test_billing_invoice_table_name_and_default_status():
    assert BillingInvoice.__tablename__ == "billing_invoices"
    row = BillingInvoice(
        tenant_id="11111111-1111-1111-1111-111111111111",
        target_year_month="2026-09",
    )
    assert row.target_year_month == "2026-09"


def test_billing_line_item_table_name():
    assert BillingLineItem.__tablename__ == "billing_line_items"
    row = BillingLineItem(
        invoice_id="22222222-2222-2222-2222-222222222222",
        item_key="data_points",
        quantity=1000,
        unit_price=Decimal("0.01"),
        amount=10,
    )
    assert row.amount == 10
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_billing_models.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.models.billing'`）

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/models/billing.py` を新規作成する。

```python
import uuid
from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class BillingUnitPrice(Base):
    __tablename__ = "billing_unit_prices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    item_key = Column(String(50), nullable=False)
    unit_price = Column(Numeric(12, 4), nullable=False)
    effective_from = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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


class BillingLineItem(Base):
    __tablename__ = "billing_line_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("billing_invoices.id", ondelete="CASCADE"), nullable=False)
    item_key = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 4), nullable=False)
    amount = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_billing_models.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add core-api/app/models/billing.py core-api/tests/test_billing_models.py
git commit -m "feat(billing): 単価マスタ・請求書・明細行のSQLAlchemyモデルを追加"
```

---

### Task 4: 積算エンジン（純粋関数）を実装する

DB・外部サービスに一切依存しない、利用量と単価から明細行・合計金額を計算するだけの関数。仕様書4.1節の端数処理ルール（行ごとに切り捨て→合計、消費税も切り捨て）をここで保証する。

**Files:**
- Create: `core-api/app/services/billing.py`
- Test: `core-api/tests/test_billing_calc.py`

**Interfaces:**
- Consumes: なし
- Produces: `calc_line_item_amount(quantity: int, unit_price: Decimal) -> int`、`calculate_invoice(usage: dict[str, int], unit_prices: dict[str, Decimal], tax_rate: Decimal = Decimal("0.10")) -> dict` — 戻り値の形は `{"line_items": [{"item_key": str, "quantity": int, "unit_price": Decimal, "amount": int}, ...], "subtotal": int, "tax_amount": int, "total_amount": int}`。Task 5（単価マスタ）・Task 6（router）がこれらを import する。`ITEM_KEYS` 定数（`tuple[str, ...]`、5つの課金項目キー）もここで定義し、Task 5 がバリデーションに使う。

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_billing_calc.py` を新規作成する。

```python
from decimal import Decimal
from app.services.billing import calc_line_item_amount, calculate_invoice


def test_calc_line_item_amount_exact_multiplication():
    assert calc_line_item_amount(1000, Decimal("0.015")) == 15


def test_calc_line_item_amount_floors_fractional_yen():
    assert calc_line_item_amount(999, Decimal("0.015")) == 14  # 14.985 -> 14


def test_calc_line_item_amount_zero_price_is_zero():
    assert calc_line_item_amount(500, Decimal("0")) == 0


def test_calc_line_item_amount_zero_quantity_is_zero():
    assert calc_line_item_amount(0, Decimal("100")) == 0


def test_calculate_invoice_sums_line_items_and_applies_tax():
    usage = {"base_fee": 1, "data_points": 10000, "alert_events": 3}
    unit_prices = {
        "base_fee": Decimal("5000"),
        "data_points": Decimal("0.01"),
        "alert_events": Decimal("10"),
    }
    result = calculate_invoice(usage, unit_prices)
    assert result["subtotal"] == 5130  # 5000 + 100 + 30
    assert result["tax_amount"] == 513  # floor(5130 * 0.10)
    assert result["total_amount"] == 5643
    assert len(result["line_items"]) == 3


def test_calculate_invoice_floors_tax_amount():
    usage = {"base_fee": 1}
    unit_prices = {"base_fee": Decimal("999")}
    result = calculate_invoice(usage, unit_prices)
    assert result["subtotal"] == 999
    assert result["tax_amount"] == 99  # floor(99.9)
    assert result["total_amount"] == 1098


def test_calculate_invoice_missing_price_defaults_to_zero():
    usage = {"device_count": 42}
    result = calculate_invoice(usage, unit_prices={})
    assert result["line_items"][0]["item_key"] == "device_count"
    assert result["line_items"][0]["amount"] == 0
    assert result["subtotal"] == 0
    assert result["tax_amount"] == 0


def test_calculate_invoice_line_item_preserves_quantity_and_unit_price():
    usage = {"alert_events": 7}
    unit_prices = {"alert_events": Decimal("50")}
    result = calculate_invoice(usage, unit_prices)
    item = result["line_items"][0]
    assert item["quantity"] == 7
    assert item["unit_price"] == Decimal("50")
    assert item["amount"] == 350
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_billing_calc.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.billing'`）

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/services/billing.py` を新規作成する。

```python
from decimal import Decimal, ROUND_DOWN

ITEM_KEYS = ("base_fee", "data_points", "device_count", "provisionable_devices", "alert_events")

DEFAULT_TAX_RATE = Decimal("0.10")


def calc_line_item_amount(quantity: int, unit_price: Decimal) -> int:
    """quantity × unit_price を円未満切り捨てで整数円にする。"""
    return int((Decimal(quantity) * unit_price).to_integral_value(rounding=ROUND_DOWN))


def calculate_invoice(
    usage: dict[str, int],
    unit_prices: dict[str, Decimal],
    tax_rate: Decimal = DEFAULT_TAX_RATE,
) -> dict:
    """利用量(usage)と単価(unit_prices)から明細行・小計・消費税・合計金額を計算する。
    unit_prices に登録されていない item_key は単価0円として扱う（明細行自体は出力する）。"""
    line_items = []
    subtotal = 0
    for item_key, quantity in usage.items():
        unit_price = unit_prices.get(item_key, Decimal("0"))
        amount = calc_line_item_amount(quantity, unit_price)
        line_items.append({
            "item_key": item_key,
            "quantity": quantity,
            "unit_price": unit_price,
            "amount": amount,
        })
        subtotal += amount

    tax_amount = int((Decimal(subtotal) * tax_rate).to_integral_value(rounding=ROUND_DOWN))
    total_amount = subtotal + tax_amount

    return {
        "line_items": line_items,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
    }
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_billing_calc.py -v`
Expected: PASS（7件全て）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/billing.py core-api/tests/test_billing_calc.py
git commit -m "feat(billing): 明細行・消費税・合計金額を計算する積算エンジンを実装"
```

---

### Task 5: 単価マスタのサービス層（取得・設定）を実装する

テナントごとの単価を取得（対象月時点で有効な単価を item_key ごとに1件ずつ）・設定（翌月以降の日付のみ許可）する。

**Files:**
- Modify: `core-api/app/services/billing.py`（Task 4 のファイルに追記）
- Test: `core-api/tests/test_billing_prices.py`

**Interfaces:**
- Consumes: `ITEM_KEYS`（Task 4）、`app.models.billing.BillingUnitPrice`（Task 3）
- Produces: `get_effective_unit_prices(db: Session, tenant_id: str, year: int, month: int) -> dict[str, Decimal]`、`set_unit_price(db: Session, tenant_id: str, item_key: str, unit_price: Decimal, effective_from: date) -> BillingUnitPrice`、`InvalidEffectiveDateError(Exception)` — Task 7（router）がこれらを import する

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_billing_prices.py` を新規作成する。

```python
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from app.services.billing import (
    InvalidEffectiveDateError,
    get_effective_unit_prices,
    set_unit_price,
)


def _price_row(item_key, unit_price, effective_from):
    row = MagicMock()
    row.item_key = item_key
    row.unit_price = unit_price
    row.effective_from = effective_from
    return row


def test_get_effective_unit_prices_picks_latest_row_per_item():
    mock_db = MagicMock()
    # サービス側は item_key 昇順・effective_from 降順で取得する想定なので、
    # モックにもその順序で渡す（同じ item_key は新しい effective_from が先）。
    rows = [
        _price_row("base_fee", Decimal("6000"), date(2026, 8, 1)),
        _price_row("base_fee", Decimal("5000"), date(2026, 6, 1)),
        _price_row("data_points", Decimal("0.02"), date(2026, 7, 1)),
    ]
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

    result = get_effective_unit_prices(mock_db, "tenant-1", 2026, 9)

    assert result == {"base_fee": Decimal("6000"), "data_points": Decimal("0.02")}


def test_get_effective_unit_prices_empty_when_no_rows():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    result = get_effective_unit_prices(mock_db, "tenant-1", 2026, 9)
    assert result == {}


def test_set_unit_price_rejects_non_first_of_month():
    mock_db = MagicMock()
    with pytest.raises(InvalidEffectiveDateError):
        set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("5000"), date(2099, 1, 15))


def test_set_unit_price_rejects_current_month_or_past():
    mock_db = MagicMock()
    current_month_start = date.today().replace(day=1)
    with pytest.raises(InvalidEffectiveDateError):
        set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("5000"), current_month_start)


def test_set_unit_price_rejects_unknown_item_key():
    mock_db = MagicMock()
    with pytest.raises(ValueError):
        set_unit_price(mock_db, "tenant-1", "not_a_real_key", Decimal("100"), date(2099, 1, 1))


def test_set_unit_price_creates_new_row_for_future_month():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("5000"), date(2099, 1, 1))

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.tenant_id == "tenant-1"
    assert added.item_key == "base_fee"
    assert added.unit_price == Decimal("5000")
    assert added.effective_from == date(2099, 1, 1)
    mock_db.commit.assert_called_once()


def test_set_unit_price_updates_existing_row_for_same_month():
    mock_db = MagicMock()
    existing = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = existing

    set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("7000"), date(2099, 1, 1))

    assert existing.unit_price == Decimal("7000")
    mock_db.add.assert_not_called()
    mock_db.commit.assert_called_once()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_billing_prices.py -v`
Expected: FAIL（`ImportError: cannot import name 'get_effective_unit_prices'`）

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/services/billing.py` の末尾に追記する。

```python
from datetime import date
from sqlalchemy.orm import Session
from app.models.billing import BillingUnitPrice


class InvalidEffectiveDateError(Exception):
    pass


def get_effective_unit_prices(db: Session, tenant_id: str, year: int, month: int) -> dict[str, Decimal]:
    """対象年月の1日時点で有効な単価を item_key ごとに1件ずつ返す
    （同じ item_key に複数の履歴がある場合、対象月以前で最も新しい effective_from の行を採用する）。"""
    target = date(year, month, 1)
    rows = (
        db.query(BillingUnitPrice)
        .filter(BillingUnitPrice.tenant_id == tenant_id, BillingUnitPrice.effective_from <= target)
        .order_by(BillingUnitPrice.item_key, BillingUnitPrice.effective_from.desc())
        .all()
    )
    result: dict[str, Decimal] = {}
    for row in rows:
        if row.item_key not in result:
            result[row.item_key] = Decimal(str(row.unit_price))
    return result


def set_unit_price(db: Session, tenant_id: str, item_key: str, unit_price: Decimal, effective_from: date) -> BillingUnitPrice:
    """単価を設定する。同じ tenant_id・item_key・effective_from の行が既にあれば更新、なければ新規作成する。
    effective_from は必ず月初日かつ「翌月以降」でなければならない（当月中の変更・日割りは許可しない）。"""
    if item_key not in ITEM_KEYS:
        raise ValueError(f"Unknown item_key: {item_key}")
    if effective_from.day != 1:
        raise InvalidEffectiveDateError("effective_from must be the first day of a month")
    current_month_start = date.today().replace(day=1)
    if effective_from <= current_month_start:
        raise InvalidEffectiveDateError("effective_from must be a future month (price changes always apply from next month)")

    existing = db.query(BillingUnitPrice).filter(
        BillingUnitPrice.tenant_id == tenant_id,
        BillingUnitPrice.item_key == item_key,
        BillingUnitPrice.effective_from == effective_from,
    ).first()
    if existing:
        existing.unit_price = unit_price
        db.commit()
        db.refresh(existing)
        return existing

    row = BillingUnitPrice(tenant_id=tenant_id, item_key=item_key, unit_price=unit_price, effective_from=effective_from)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
```

ファイル先頭のimport文もあわせて整理する（`from decimal import Decimal, ROUND_DOWN` の下に `from datetime import date` と `from sqlalchemy.orm import Session` と `from app.models.billing import BillingUnitPrice` を追加。上記コードブロックでは分かりやすさのため使用箇所の近くに書いたが、実装時はファイル先頭にまとめる）。

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_billing_prices.py -v`
Expected: PASS（7件全て）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/billing.py core-api/tests/test_billing_prices.py
git commit -m "feat(billing): 単価マスタの取得・設定サービスを実装（翌月適用ルール込み）"
```

---

### Task 6: billingのPydanticスキーマを追加する

**Files:**
- Create: `core-api/app/schemas/billing.py`

**Interfaces:**
- Consumes: なし
- Produces: `UnitPriceSet(item_key: ItemKey, unit_price: str, effective_from: date)`、`UnitPriceOut(item_key: str, unit_price: str, effective_from: date)` — Task 7（router）がこれらを使う。`unit_price` を `str` にしているのは、`Decimal` を FastAPI のレスポンスで JSON にする際のシリアライズ形式（数値になるか文字列になるか）を曖昧にしないため。ルーター側で明示的に `Decimal(...)` ⇔ `str(...)` に変換する。

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_billing_schemas.py` を新規作成する。

```python
from datetime import date
import pytest
from pydantic import ValidationError
from app.schemas.billing import UnitPriceSet, UnitPriceOut


def test_unit_price_set_accepts_valid_item_key():
    body = UnitPriceSet(item_key="base_fee", unit_price="5000", effective_from=date(2099, 1, 1))
    assert body.item_key == "base_fee"
    assert body.unit_price == "5000"


def test_unit_price_set_rejects_unknown_item_key():
    with pytest.raises(ValidationError):
        UnitPriceSet(item_key="not_a_real_key", unit_price="5000", effective_from=date(2099, 1, 1))


def test_unit_price_out_serializes_as_string_price():
    out = UnitPriceOut(item_key="data_points", unit_price="0.01", effective_from=date(2026, 9, 1))
    assert out.model_dump()["unit_price"] == "0.01"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_billing_schemas.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.schemas.billing'`）

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/schemas/billing.py` を新規作成する。

```python
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field

ItemKey = Literal["base_fee", "data_points", "device_count", "provisionable_devices", "alert_events"]


class UnitPriceSet(BaseModel):
    item_key: ItemKey
    unit_price: str = Field(description="10進数の文字列（例: \"0.015\"）。Decimalとして解釈される")
    effective_from: date


class UnitPriceOut(BaseModel):
    item_key: str
    unit_price: str
    effective_from: date
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_billing_schemas.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add core-api/app/schemas/billing.py core-api/tests/test_billing_schemas.py
git commit -m "feat(billing): 単価マスタ用のPydanticスキーマを追加"
```

---

### Task 7: billing router（単価マスタのPF管理者向けAPI）を実装する

**Files:**
- Create: `core-api/app/routers/billing.py`
- Modify: `core-api/app/main.py`（import と `include_router` 呼び出しの追加）
- Test: `core-api/tests/test_billing_api.py`

**Interfaces:**
- Consumes: `app.schemas.billing.UnitPriceSet`/`UnitPriceOut`（Task 6）、`app.services.billing.get_effective_unit_prices`/`set_unit_price`/`InvalidEffectiveDateError`（Task 5）
- Produces: `GET /tenants/{tenant_id}/billing/prices` → `list[UnitPriceOut]`、`POST /tenants/{tenant_id}/billing/prices` → `UnitPriceOut`（PF管理者認証必須）— Phase 2 のrouterタスクがこのファイルに請求書関連のエンドポイントを追加する

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_billing_api.py` を新規作成する。

```python
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.billing import InvalidEffectiveDateError

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token():
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": "admin",
    })


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_list_current_prices_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/prices")
    assert resp.status_code == 401


def test_list_current_prices_rejects_tenant_token():
    resp = client.get(
        f"/tenants/{TENANT_ID}/billing/prices",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert resp.status_code == 401


def test_list_current_prices_returns_effective_prices():
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.get_effective_unit_prices",
               return_value={"base_fee": Decimal("5000"), "data_points": Decimal("0.01")}):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/prices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_key = {item["item_key"]: item["unit_price"] for item in body}
    assert by_key == {"base_fee": "5000", "data_points": "0.01"}


def test_create_price_success():
    created = MagicMock()
    created.item_key = "base_fee"
    created.unit_price = Decimal("6000")
    created.effective_from = date(2099, 1, 1)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.set_unit_price", return_value=created) as mock_set:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body == {"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"}
    assert mock_set.call_args[0][3] == Decimal("6000")


def test_create_price_rejects_invalid_effective_date():
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.set_unit_price",
               side_effect=InvalidEffectiveDateError("must be a future month")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2020-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_rejects_malformed_unit_price_string():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "not-a-number", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_requires_platform_auth():
    resp = client.post(
        f"/tenants/{TENANT_ID}/billing/prices",
        json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_billing_api.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.routers.billing'`、または404 — ルーターが存在しないため）

- [ ] **Step 3: 最小限の実装をする**

`core-api/app/routers/billing.py` を新規作成する。

```python
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database import SessionLocal
from app.schemas.billing import UnitPriceOut, UnitPriceSet
from app.services.auth import verify_token
from app.services.audit import log_audit
from app.services.billing import (
    InvalidEffectiveDateError,
    get_effective_unit_prices,
    set_unit_price,
)

router = APIRouter(prefix="/tenants/{tenant_id}/billing", tags=["billing"])
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _validate_uuid(value: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid tenant_id")
    return value.lower()


@router.get("/prices", response_model=list[UnitPriceOut])
def list_current_prices(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        prices = get_effective_unit_prices(db, tenant_id, now.year, now.month)
    month_start = date(now.year, now.month, 1)
    return [
        {"item_key": item_key, "unit_price": str(unit_price), "effective_from": month_start}
        for item_key, unit_price in prices.items()
    ]


@router.post("/prices", response_model=UnitPriceOut, status_code=status.HTTP_201_CREATED)
def create_price(tenant_id: str, body: UnitPriceSet, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    try:
        unit_price = Decimal(body.unit_price)
    except InvalidOperation:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price must be a decimal number")
    if unit_price < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unit_price must not be negative")

    with SessionLocal() as db:
        try:
            row = set_unit_price(db, tenant_id, body.item_key, unit_price, body.effective_from)
        except InvalidEffectiveDateError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        result = {
            "item_key": row.item_key,
            "unit_price": str(row.unit_price),
            "effective_from": row.effective_from,
        }

    log_audit("platform", payload["sub"], payload["email"], "set_billing_unit_price",
              tenant_id=tenant_id, resource_type="billing_unit_price",
              detail={"item_key": body.item_key, "unit_price": body.unit_price,
                      "effective_from": body.effective_from.isoformat()})
    return result
```

`core-api/app/main.py` に router を登録する。

`from app.routers import ...` の並びに `billing` を追加し（既存の import 文の並びに合わせる）、`app.include_router(device_groups.router)` の直後に以下を追加する。

```python
app.include_router(billing.router)
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_billing_api.py -v`
Expected: PASS（7件全て）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/routers/billing.py core-api/app/main.py core-api/tests/test_billing_api.py
git commit -m "feat(billing): 単価マスタのPF管理者向けAPI（一覧・設定）を追加"
```

---

### Task 8: 全体テストスイートを実行して結線を確認する

**Files:**
- なし（変更なし、検証のみ）

**Interfaces:**
- Consumes: Task 1〜7 の全成果物
- Produces: なし

- [ ] **Step 1: billing関連の全テストをまとめて実行する**

Run: `python -m pytest tests/test_billing_calc.py tests/test_billing_prices.py tests/test_billing_schemas.py tests/test_billing_api.py tests/test_billing_models.py tests/test_db.py tests/test_tenant_portal_tokens.py -v`
Expected: 全てPASS

- [ ] **Step 2: 既存の全テストスイートを実行し、リグレッションがないことを確認する**

Run: `python -m pytest -q`
Expected: 実行前と同じ失敗件数・同じ失敗テスト名のみ（このPlan着手前から存在する、DB未接続・bcrypt制限等に起因する既存の失敗はそのまま。billing関連の新規失敗が0件であること）

- [ ] **Step 3: 起動確認（マイグレーションがエラーなく通ることの確認）**

Run: `python -c "from app.main import app; print('OK')"`
Expected: `OK` が出力される（importエラーが起きない）

このタスクはコード変更を伴わないため、コミットは不要（Task 1〜7 のコミットで完結している）。

---

## Phase 2 への申し送り

- InfluxDBからの月指定版クエリ（当月データポイント数・当月ユニークデバイス数）を新規実装する。既存の `app/routers/stats.py` の `_count_influxdb_points`/`_parse_influx_csv_scalar` は「常に現在の月」しか扱えないため、そのままでは使えない（`_parse_influx_csv_scalar` はそのまま再利用可、レンジ計算部分のみ月指定対応が必要）
- `provisionable_devices` は `app.routers.stats._calc_provisionable_devices(db, tenant_id, schema)` をそのまま再利用する（仕様書2.2節で確定済み。課金計算層での無制限テナント特別扱いは不要）
- `billing_invoices`/`billing_line_items` への実際の書き込み（draft作成・再計算・`finalized`への遷移・`corrected`差分追加）
- 月次バッチのトリガー方法（cronなのか、手動実行エンドポイントなのか）と、締め切りルール（仕様書2.3節: 翌月1日0:00 JST集計、遅延到着データは到着月でカウントし切り捨てない）の実装
- ビルショック閾値のデフォルト値・通知実装、個別デバイス単位の異常検知ロジック（仕様書2.5節・5節、未確定）
- 消費税率のマスタ化（現状はPhase 1で `calculate_invoice` にハードコード。仕様書5節で「ハードコードか税率マスタか」は未確定のまま）
- 単価管理・請求書確認のPF管理者向けUI画面（仕様書3節「billingメニュー」。Phase 1・2ともバックエンドAPIのみ）
