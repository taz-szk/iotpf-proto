# 料金積算機能 Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 月次の実利用量をInfluxDB/PostgreSQLから集計し、`billing_invoices`/`billing_line_items`へ実際の請求書（draft→finalized）を自動生成するバッチを実装し、テナント管理者が自テナントの請求書（当月推定額＋過去の確定済み請求書）を確認できるUIを追加する。

**Architecture:** core-api内に新規サービス2本（利用量集計・バッチ本体）を追加し、既存の`start_audit_purge_worker`と同じ「daemon threadで無限ループ+`time.sleep`」パターンで毎日実行する。API層はテナントポータル（自テナント参照）とPF管理者向け（既存`billing.py`への追記、サポート用途）の2系統。UIはテナントポータルの既存タブ群の末尾に「請求」タブを1つ追加する。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, PostgreSQL, InfluxDB (Flux), Alpine.js, Tailwind CSS

**Spec:** docs/superpowers/specs/2026-09-10-billing-calculation-phase2-design.md

## Global Constraints

- 消費税率は10%ハードコード（既存`app/services/billing.py`の`DEFAULT_TAX_RATE`をそのまま使う。今回変更しない）
- `billing_invoices`/`billing_line_items`のテーブル・カラムは変更しない（Phase 1で作成済み、マイグレーション追加なし）
- バッチの定期実行は**APSchedulerを使わない**。既存`app/services/audit.py`の`start_audit_purge_worker`と同じ`threading.Thread(daemon=True)` + `while True: ...; time.sleep(86400)`パターンに統一する（core-apiにAPSchedulerの依存を増やさない）
- テナントポータルAPI（`/tenant-portal/me/billing/...`）の閲覧権限はviewer/operator/admin全員（既存の`_require_tenant` dependencyのみ、role制限なし。統計タブと同じ扱い）
- finalizedへの遷移は「対象月が変わったときにバッチが自動で行う」の1経路のみ。手動確定・修正(corrected)機能は今回実装しない
- `provisionable_devices`の値は「現在時点のスナップショット」であり過去月を再現できない。draft更新時（＝対象が今月のときのみ）に呼ぶこと。finalized済みの行を再計算してはならない
- 対象年月の判定は既存`app/routers/stats.py`の`_month_start_rfc3339`と同様、UTC基準の`datetime.now(timezone.utc)`を使う（JSTへの厳密な変換はしない。既存コードの慣習に合わせる）

---

### Task 1: 月次利用量集計サービス

**Files:**
- Create: `core-api/app/services/billing_usage.py`
- Test: `core-api/tests/test_billing_usage.py`

**Interfaces:**
- Consumes: `app.routers.stats._calc_provisionable_devices(db, tenant_id, schema) -> tuple[int, bool]`（既存）
- Produces: `aggregate_monthly_usage(db, tenant_id: str, schema: str, influxdb_org_id: str, influxdb_token: str, year: int, month: int) -> dict[str, int]` — キーは`app.services.billing.ITEM_KEYS`の5種すべて（`base_fee`, `data_points`, `device_count`, `provisionable_devices`, `alert_events`）。Task 2がこの関数をそのまま呼ぶ。

- [ ] **Step 1: Write the failing tests**

`core-api/tests/test_billing_usage.py`:

```python
from unittest.mock import patch, MagicMock
from app.services.billing_usage import (
    _month_range_rfc3339,
    _count_influxdb_points_for_month,
    _count_unique_devices_for_month,
    _count_alert_events_for_month,
    aggregate_monthly_usage,
)


def test_month_range_rfc3339_normal_month():
    start, stop = _month_range_rfc3339(2026, 9)
    assert start == "2026-09-01T00:00:00Z"
    assert stop == "2026-10-01T00:00:00Z"


def test_month_range_rfc3339_december_rolls_to_next_year():
    start, stop = _month_range_rfc3339(2026, 12)
    assert start == "2026-12-01T00:00:00Z"
    assert stop == "2027-01-01T00:00:00Z"


def test_count_influxdb_points_for_month_parses_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,999\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_points_for_month("org-1", "tok", 2026, 9)
    assert result == 999
    call_kwargs = mock_httpx.post.call_args
    assert "2026-09-01T00:00:00Z" in call_kwargs.kwargs["json"]["query"]
    assert "2026-10-01T00:00:00Z" in call_kwargs.kwargs["json"]["query"]


def test_count_influxdb_points_for_month_returns_zero_on_error_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_points_for_month("org-1", "tok", 2026, 9)
    assert result == 0


def test_count_unique_devices_for_month_parses_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,7\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_unique_devices_for_month("org-1", "tok", 2026, 9)
    assert result == 7


def test_count_alert_events_for_month_queries_target_month():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = 3
    result = _count_alert_events_for_month(mock_db, "tenant_x", 2026, 9)
    assert result == 3
    sql_text = str(mock_db.execute.call_args[0][0])
    assert "alert_events" in sql_text


def test_aggregate_monthly_usage_returns_all_item_keys():
    mock_db = MagicMock()
    with patch("app.services.billing_usage._count_influxdb_points_for_month", return_value=100), \
         patch("app.services.billing_usage._count_unique_devices_for_month", return_value=5), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=2), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(40, False)):
        usage = aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    assert usage == {
        "base_fee": 1,
        "data_points": 100,
        "device_count": 5,
        "provisionable_devices": 40,
        "alert_events": 2,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && python -m pytest tests/test_billing_usage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.billing_usage'`

- [ ] **Step 3: Write the implementation**

`core-api/app/services/billing_usage.py`:

```python
from datetime import datetime, timezone
import httpx

from app.config import settings
from app.routers.stats import _parse_influx_csv_scalar, _calc_provisionable_devices


def _month_range_rfc3339(year: int, month: int) -> tuple[str, str]:
    """対象年月の[月初, 翌月初)をRFC3339(UTC)の文字列ペアで返す。"""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        stop = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        stop = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start.strftime('%Y-%m-%dT%H:%M:%SZ'), stop.strftime('%Y-%m-%dT%H:%M:%SZ')


def _count_influxdb_points_for_month(influxdb_org_id: str, token: str, year: int, month: int) -> int:
    """対象年月に処理・保存されたテレメトリの総数。"""
    start, stop = _month_range_rfc3339(year, month)
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {start}, stop: {stop})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> group()\n'
        '  |> count()\n'
        '  |> sum()\n'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={influxdb_org_id}",
            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
            json={"query": query, "type": "flux"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return 0
        return _parse_influx_csv_scalar(resp.text)
    except Exception:
        return 0


def _count_unique_devices_for_month(influxdb_org_id: str, token: str, year: int, month: int) -> int:
    """対象年月にテレメトリを送信したユニークdevice_name数。"""
    start, stop = _month_range_rfc3339(year, month)
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {start}, stop: {stop})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> keep(columns: ["device_name"])\n'
        '  |> group()\n'
        '  |> distinct(column: "device_name")\n'
        '  |> group()\n'
        '  |> count()\n'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={influxdb_org_id}",
            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
            json={"query": query, "type": "flux"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return 0
        return _parse_influx_csv_scalar(resp.text)
    except Exception:
        return 0


def _count_alert_events_for_month(db, schema: str, year: int, month: int) -> int:
    """対象年月に発報されたアラート総数。"""
    from sqlalchemy import text
    start, stop = _month_range_rfc3339(year, month)
    result = db.execute(text(f'''
        SELECT COUNT(*) FROM "{schema}".alert_events
        WHERE triggered_at >= :start AND triggered_at < :stop
    '''), {"start": start, "stop": stop}).scalar()
    return result or 0


def aggregate_monthly_usage(
    db, tenant_id: str, schema: str, influxdb_org_id: str, influxdb_token: str,
    year: int, month: int,
) -> dict[str, int]:
    """対象年月の利用量を集計し、app.services.billing.calculate_invoice()に渡せる
    usage dictを返す。provisionable_devicesは「現在時点」のスナップショットなので、
    finalized済みの月に対しては呼び出し側が絶対に呼ばないこと。"""
    provisionable_devices, _has_unlimited = _calc_provisionable_devices(db, tenant_id, schema)
    return {
        "base_fee": 1,
        "data_points": _count_influxdb_points_for_month(influxdb_org_id, influxdb_token, year, month),
        "device_count": _count_unique_devices_for_month(influxdb_org_id, influxdb_token, year, month),
        "provisionable_devices": provisionable_devices,
        "alert_events": _count_alert_events_for_month(db, schema, year, month),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && python -m pytest tests/test_billing_usage.py -v`
Expected: PASS（7件）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/billing_usage.py core-api/tests/test_billing_usage.py
git commit -m "feat(billing): 月次利用量集計サービスを実装"
```

---

### Task 2: 請求書生成バッチ

**Files:**
- Create: `core-api/app/services/billing_batch.py`
- Test: `core-api/tests/test_billing_batch.py`

**Interfaces:**
- Consumes: `aggregate_monthly_usage(...)`（Task 1）、`app.services.billing.get_effective_unit_prices(db, tenant_id, year, month) -> dict[str, Decimal]`（既存）、`app.services.billing.calculate_invoice(usage, unit_prices) -> dict`（既存、`{"line_items": [...], "subtotal": int, "tax_amount": int, "total_amount": int}`を返す）、`app.models.public.Tenant`（既存）、`app.models.billing.BillingInvoice`/`BillingLineItem`（既存）
- Produces: `run_monthly_billing_batch() -> list[dict]`（各要素`{"tenant_id": str, "status": "ok"|"skipped_not_draft"|"error", ...}`）と`start_billing_batch_worker() -> None`。Task 3がこの2つ目をimportして呼ぶ。

- [ ] **Step 1: Write the failing tests**

`core-api/tests/test_billing_batch.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
from app.services.billing_batch import (
    _current_target_year_month,
    _finalize_stale_drafts,
    _get_or_create_draft_invoice,
    _replace_line_items,
    run_monthly_billing_batch,
)


def test_current_target_year_month_formats_correctly():
    fixed_now = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)
    with patch("app.services.billing_batch.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        year, month, ym = _current_target_year_month()
    assert (year, month, ym) == (2026, 9, "2026-09")


def test_finalize_stale_drafts_updates_old_draft_only():
    old_draft = MagicMock(status="draft", target_year_month="2026-08")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [old_draft]

    _finalize_stale_drafts(mock_db, "tenant-1", "2026-09")

    assert old_draft.status == "finalized"
    assert old_draft.finalized_at is not None
    mock_db.commit.assert_called_once()


def test_finalize_stale_drafts_noop_when_nothing_stale():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    _finalize_stale_drafts(mock_db, "tenant-1", "2026-09")

    mock_db.commit.assert_not_called()


def test_get_or_create_draft_invoice_creates_when_missing():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    _get_or_create_draft_invoice(mock_db, "tenant-1", "2026-09")

    assert mock_db.add.called
    assert mock_db.commit.called


def test_get_or_create_draft_invoice_returns_existing():
    existing = MagicMock(status="draft")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = existing

    result = _get_or_create_draft_invoice(mock_db, "tenant-1", "2026-09")

    assert result is existing
    mock_db.add.assert_not_called()


def test_replace_line_items_deletes_then_inserts():
    mock_db = MagicMock()
    line_items = [
        {"item_key": "base_fee", "quantity": 1, "unit_price": Decimal("5000"), "amount": 5000},
    ]

    _replace_line_items(mock_db, "invoice-1", line_items)

    mock_db.query.return_value.filter.return_value.delete.assert_called_once()
    assert mock_db.add.call_count == 1
    mock_db.commit.assert_called_once()


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
    mock_tenant_db.query.return_value.filter.return_value.first.return_value = draft_invoice
    mock_tenant_db.query.return_value.filter.return_value.all.return_value = []  # 失効対象のdraftなし

    with patch("app.services.billing_batch.SessionLocal", side_effect=[mock_list_db, mock_tenant_db]), \
         patch("app.services.billing_batch.aggregate_monthly_usage", return_value={"base_fee": 1}), \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={"base_fee": Decimal("5000")}), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [{"item_key": "base_fee", "quantity": 1, "unit_price": Decimal("5000"), "amount": 5000}],
             "subtotal": 5000, "tax_amount": 500, "total_amount": 5500,
         }):
        results = run_monthly_billing_batch()

    assert results == [{"tenant_id": "tenant-1", "status": "ok", "total_amount": 5500}]
    assert draft_invoice.subtotal == 5000
    assert draft_invoice.total_amount == 5500


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
    mock_tenant_db.query.return_value.filter.return_value.first.return_value = already_finalized
    mock_tenant_db.query.return_value.filter.return_value.all.return_value = []

    with patch("app.services.billing_batch.SessionLocal", side_effect=[mock_list_db, mock_tenant_db]), \
         patch("app.services.billing_batch.aggregate_monthly_usage", return_value={"base_fee": 1}), \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={}), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [], "subtotal": 0, "tax_amount": 0, "total_amount": 0,
         }):
        results = run_monthly_billing_batch()

    assert results == [{"tenant_id": "tenant-1", "status": "skipped_not_draft"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && python -m pytest tests/test_billing_batch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.billing_batch'`

- [ ] **Step 3: Write the implementation**

`core-api/app/services/billing_batch.py`:

```python
import threading
import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.public import Tenant
from app.models.billing import BillingInvoice, BillingLineItem
from app.services.billing import calculate_invoice, get_effective_unit_prices
from app.services.billing_usage import aggregate_monthly_usage


def _current_target_year_month() -> tuple[int, int, str]:
    now = datetime.now(timezone.utc)
    return now.year, now.month, f"{now.year:04d}-{now.month:02d}"


def _finalize_stale_drafts(db, tenant_id: str, current_target_year_month: str) -> None:
    """対象月が変わったdraft請求書をfinalizedに確定する。finalized遷移はこの1経路のみ。"""
    stale = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.status == "draft",
        BillingInvoice.target_year_month != current_target_year_month,
    ).all()
    for invoice in stale:
        invoice.status = "finalized"
        invoice.finalized_at = datetime.now(timezone.utc)
    if stale:
        db.commit()


def _get_or_create_draft_invoice(db, tenant_id: str, target_year_month: str) -> BillingInvoice:
    invoice = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.target_year_month == target_year_month,
    ).first()
    if invoice is None:
        invoice = BillingInvoice(tenant_id=tenant_id, target_year_month=target_year_month, status="draft")
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
    return invoice


def _replace_line_items(db, invoice_id, line_items: list[dict]) -> None:
    db.query(BillingLineItem).filter(BillingLineItem.invoice_id == invoice_id).delete()
    for li in line_items:
        db.add(BillingLineItem(
            invoice_id=invoice_id, item_key=li["item_key"], quantity=li["quantity"],
            unit_price=li["unit_price"], amount=li["amount"],
        ))
    db.commit()


def run_monthly_billing_batch() -> list[dict]:
    """全アクティブテナントについて、対象月draft請求書を再集計する。
    対象月が変わったdraftは先にfinalizedへ確定する。戻り値は各テナントの処理結果。"""
    results: list[dict] = []
    year, month, target_year_month = _current_target_year_month()

    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(Tenant.status == "active").all()
        tenant_infos = [
            (str(t.id), f"tenant_{str(t.id).replace('-', '_')}", t.influxdb_org_id or "", t.influxdb_token or "")
            for t in tenants
        ]

    for tenant_id, schema, influxdb_org_id, influxdb_token in tenant_infos:
        try:
            with SessionLocal() as db:
                _finalize_stale_drafts(db, tenant_id, target_year_month)

                invoice = _get_or_create_draft_invoice(db, tenant_id, target_year_month)
                if invoice.status != "draft":
                    results.append({"tenant_id": tenant_id, "status": "skipped_not_draft"})
                    continue

                usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
                prices = get_effective_unit_prices(db, tenant_id, year, month)
                calc = calculate_invoice(usage, prices)

                invoice.subtotal = calc["subtotal"]
                invoice.tax_amount = calc["tax_amount"]
                invoice.total_amount = calc["total_amount"]
                db.commit()

                _replace_line_items(db, invoice.id, calc["line_items"])
            results.append({"tenant_id": tenant_id, "status": "ok", "total_amount": calc["total_amount"]})
        except Exception as e:
            results.append({"tenant_id": tenant_id, "status": "error", "detail": str(e)})

    return results


def start_billing_batch_worker() -> None:
    """毎日1回、月次請求書生成バッチを実行するバックグラウンドスレッドを起動する。"""
    def _loop() -> None:
        while True:
            try:
                run_monthly_billing_batch()
            except Exception as e:
                print(f"[billing_batch] run failed: {e}")
            time.sleep(86400)
    threading.Thread(target=_loop, daemon=True).start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && python -m pytest tests/test_billing_batch.py -v`
Expected: PASS（8件）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/billing_batch.py core-api/tests/test_billing_batch.py
git commit -m "feat(billing): 請求書draft/finalized生成バッチを実装"
```

---

### Task 3: バッチワーカーをcore-api起動時に登録

**Files:**
- Modify: `core-api/app/main.py`

**Interfaces:**
- Consumes: `start_billing_batch_worker()`（Task 2）

- [ ] **Step 1: 手動確認手順を先に決める（このタスクはコード変更のみでテスト不可のため、Step構成が異なる）**

このタスクは既存の`start_audit_purge_worker()`呼び出しに1行追加するだけで、ユニットテストで検証できる新規ロジックはない（既存の`on_startup`自体もテストされていない）。実装後にimportエラーが起きないことだけをTask 4以降のテスト実行時に間接的に確認する。

- [ ] **Step 2: 実装**

`core-api/app/main.py`の該当行を編集する。

変更前:
```python
from app.services.audit import start_audit_purge_worker
```

変更後:
```python
from app.services.audit import start_audit_purge_worker
from app.services.billing_batch import start_billing_batch_worker
```

変更前:
```python
    threading.Thread(target=_run_emqx_setup, daemon=True).start()
    start_audit_purge_worker()
```

変更後:
```python
    threading.Thread(target=_run_emqx_setup, daemon=True).start()
    start_audit_purge_worker()
    start_billing_batch_worker()
```

- [ ] **Step 3: importエラーが起きないことを確認**

Run: `cd core-api && python -c "from app.main import app; print('OK')"`
Expected: `OK`（環境変数`POSTGRES_DSN`等が未設定だとエラーになるため、他タスクのテスト実行と同様に`POSTGRES_DSN`を設定してから実行する。詳細はTask 1の`conftest.py`が設定しているデフォルト値を参照）

- [ ] **Step 4: Commit**

```bash
git add core-api/app/main.py
git commit -m "feat(billing): core-api起動時に請求書生成バッチワーカーを登録"
```

---

### Task 4: PF管理者向け請求書一覧API

**Files:**
- Modify: `core-api/app/schemas/billing.py`
- Modify: `core-api/app/routers/billing.py`
- Test: `core-api/tests/test_billing_api.py`

**Interfaces:**
- Consumes: `app.models.billing.BillingInvoice`（既存）
- Produces: `GET /tenants/{tenant_id}/billing/invoices` → `list[InvoiceOut]`（PF管理者認証必須）

- [ ] **Step 1: Write the failing tests**

`core-api/tests/test_billing_api.py`に追記（既存の`TENANT_ID`/`_platform_token`/`_tenant_token`/`_session_ctx`をそのまま使う）:

```python
from app.models.billing import BillingInvoice


def _invoice_row(target_year_month, status_, subtotal, tax_amount, total_amount):
    row = MagicMock(spec=BillingInvoice)
    row.target_year_month = target_year_month
    row.status = status_
    row.subtotal = subtotal
    row.tax_amount = tax_amount
    row.total_amount = total_amount
    return row


def test_list_tenant_invoices_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/invoices")
    assert resp.status_code == 401


def test_list_tenant_invoices_returns_invoices_newest_first():
    rows = [
        _invoice_row("2026-09", "draft", 5000, 500, 5500),
        _invoice_row("2026-08", "finalized", 4000, 400, 4400),
    ]
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()  # tenant exists
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["target_year_month"] == "2026-09"
    assert body[0]["total_amount"] == 5500


def test_list_tenant_invoices_tenant_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && python -m pytest tests/test_billing_api.py -v -k invoices`
Expected: FAIL（`GET /tenants/{tenant_id}/billing/invoices` returns 404 not-found-route or `ImportError`）

- [ ] **Step 3: Write the implementation**

`core-api/app/schemas/billing.py`に追記:

```python
class InvoiceOut(BaseModel):
    target_year_month: str
    status: str
    subtotal: int
    tax_amount: int
    total_amount: int
```

`core-api/app/routers/billing.py`のimportに追記:

```python
from app.models.billing import BillingInvoice
from app.schemas.billing import InvoiceOut, UnitPriceOut, UnitPriceSet
```

（既存の`from app.schemas.billing import UnitPriceOut, UnitPriceSet`を上の2行目に置き換える）

`core-api/app/routers/billing.py`の末尾に追記:

```python
@router.get("/invoices", response_model=list[InvoiceOut])
def list_tenant_invoices(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        rows = (
            db.query(BillingInvoice)
            .filter(BillingInvoice.tenant_id == tenant_id)
            .order_by(BillingInvoice.target_year_month.desc())
            .all()
        )
        return [
            InvoiceOut(
                target_year_month=r.target_year_month, status=r.status,
                subtotal=r.subtotal, tax_amount=r.tax_amount, total_amount=r.total_amount,
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && python -m pytest tests/test_billing_api.py -v`
Expected: PASS（既存分含め全件）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/schemas/billing.py core-api/app/routers/billing.py core-api/tests/test_billing_api.py
git commit -m "feat(billing): PF管理者向け請求書一覧APIを追加"
```

---

### Task 5: テナントポータル請求書API

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_tenant_portal_billing.py`

**Interfaces:**
- Consumes: `app.models.billing.BillingInvoice`/`BillingLineItem`（既存）、`_require_tenant`（既存dependency、`tenant_portal.py`内で定義済み）
- Produces: `GET /tenant-portal/me/billing/invoices` → 一覧（dict配列）、`GET /tenant-portal/me/billing/invoices/{target_year_month}` → 明細付き詳細

- [ ] **Step 1: Write the failing tests**

`core-api/tests/test_tenant_portal_billing.py`（新規ファイル。既存`test_dashboard_panel_config.py`の`_tenant_token`パターンを流用）:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_token(role: str = "viewer"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _invoice(target_year_month, status_, subtotal=5000, tax_amount=500, total_amount=5500):
    inv = MagicMock()
    inv.target_year_month = target_year_month
    inv.status = status_
    inv.subtotal = subtotal
    inv.tax_amount = tax_amount
    inv.total_amount = total_amount
    inv.id = "invoice-1"
    return inv


def test_list_my_invoices_requires_auth():
    resp = client.get("/tenant-portal/me/billing/invoices")
    assert resp.status_code == 401


def test_list_my_invoices_allows_viewer():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _invoice("2026-09", "draft"),
        ]
        resp = client.get(
            "/tenant-portal/me/billing/invoices",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{
        "target_year_month": "2026-09", "status": "draft",
        "subtotal": 5000, "tax_amount": 500, "total_amount": 5500,
    }]


def test_get_my_invoice_detail_includes_line_items():
    from decimal import Decimal
    line_item = MagicMock()
    line_item.item_key = "base_fee"
    line_item.quantity = 1
    line_item.unit_price = Decimal("5000")
    line_item.amount = 5000

    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = _invoice("2026-09", "draft")
        mock_db.query.return_value.filter.return_value.all.return_value = [line_item]
        resp = client.get(
            "/tenant-portal/me/billing/invoices/2026-09",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_year_month"] == "2026-09"
    assert body["line_items"] == [{"item_key": "base_fee", "quantity": 1, "unit_price": "5000", "amount": 5000}]


def test_get_my_invoice_detail_not_found():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = client.get(
            "/tenant-portal/me/billing/invoices/2026-01",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_billing.py -v`
Expected: FAIL（404 Not Found、ルート未定義のため）

- [ ] **Step 3: Write the implementation**

`core-api/app/routers/tenant_portal.py`のimportに追記（既存の`from app.models.public import AuditLog, ProvisioningToken, Tenant`の直後）:

```python
from app.models.billing import BillingInvoice, BillingLineItem
```

`core-api/app/routers/tenant_portal.py`の`# 統計`セクション（`@router.get("/me/stats")`の関数）の直後に追記:

```python
# ---------------------------------------------------------------------------
# 請求
# ---------------------------------------------------------------------------

@router.get("/me/billing/invoices")
def list_my_invoices(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        rows = (
            db.query(BillingInvoice)
            .filter(BillingInvoice.tenant_id == tenant_id)
            .order_by(BillingInvoice.target_year_month.desc())
            .all()
        )
        return [
            {
                "target_year_month": r.target_year_month,
                "status": r.status,
                "subtotal": r.subtotal,
                "tax_amount": r.tax_amount,
                "total_amount": r.total_amount,
            }
            for r in rows
        ]


@router.get("/me/billing/invoices/{target_year_month}")
def get_my_invoice(target_year_month: str, payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        invoice = db.query(BillingInvoice).filter(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.target_year_month == target_year_month,
        ).first()
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        line_items = db.query(BillingLineItem).filter(BillingLineItem.invoice_id == invoice.id).all()
        return {
            "target_year_month": invoice.target_year_month,
            "status": invoice.status,
            "subtotal": invoice.subtotal,
            "tax_amount": invoice.tax_amount,
            "total_amount": invoice.total_amount,
            "line_items": [
                {
                    "item_key": li.item_key, "quantity": li.quantity,
                    "unit_price": str(li.unit_price), "amount": li.amount,
                }
                for li in line_items
            ],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_billing.py -v`
Expected: PASS（4件）

- [ ] **Step 5: Commit**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_billing.py
git commit -m "feat(billing): テナントポータル向け請求書API(一覧・詳細)を追加"
```

---

### Task 6: テナントポータルUI「請求」タブ

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: `GET /tenant-portal/me/billing/invoices`、`GET /tenant-portal/me/billing/invoices/{target_year_month}`（Task 5）、既存の`portalFetch(method, path)`ヘルパー

このタスクはUIのみでバックエンドのユニットテストは対象外。手動確認手順をStepとして記載する。

- [ ] **Step 1: タブ一覧に追加**

`admin-ui/tenant-portal.html`の`get tabs()`内、`{ id: 'stats', ... }`の直後に1行追加する（統計タブの直後＝共有タブ群の一番下、既存の配置ルールを継承）:

変更前:
```javascript
        { id: 'stats',            label: '統計',                     icon: '📊' },
      ];
```

変更後:
```javascript
        { id: 'stats',            label: '統計',                     icon: '📊' },
        { id: 'billing',          label: '請求',                     icon: '💰' },
      ];
```

- [ ] **Step 2: 状態変数を追加**

`stats: null, statsLoading: false,`の直後に追加:

```javascript
    // 統計
    stats: null, statsLoading: false,
    // 請求
    invoices: [], invoicesLoading: false,
    expandedInvoiceMonth: null, invoiceDetail: null, invoiceDetailLoading: false,
```

- [ ] **Step 3: switchTabに分岐を追加**

`async switchTab(tab) {`内、`if (tab === 'stats') await this.loadStats();`の直後に追加:

```javascript
      if (tab === 'stats')    await this.loadStats();
      if (tab === 'billing')  await this.loadInvoices();
```

- [ ] **Step 4: メソッドを追加**

`provisionableLabel(s) { ... }`の直後に追加:

```javascript
    async loadInvoices() {
      this.invoicesLoading = true;
      try { this.invoices = await portalFetch('GET', '/me/billing/invoices') || []; }
      catch(e) { this.error = e.message; } finally { this.invoicesLoading = false; }
    },
    async toggleInvoice(month) {
      if (this.expandedInvoiceMonth === month) { this.expandedInvoiceMonth = null; return; }
      this.expandedInvoiceMonth = month;
      this.invoiceDetail = null;
      this.invoiceDetailLoading = true;
      try { this.invoiceDetail = await portalFetch('GET', `/me/billing/invoices/${month}`); }
      catch(e) { this.error = e.message; } finally { this.invoiceDetailLoading = false; }
    },
    invoiceItemLabel(key) {
      const labels = {
        base_fee: '基本料金', data_points: 'データポイント', device_count: 'デバイス数',
        provisionable_devices: 'プロビジョニング可能数', alert_events: 'アラートイベント',
      };
      return labels[key] || key;
    },
```

- [ ] **Step 5: HTMLパネルを追加**

統計パネル（`<!-- ===== 統計 ===== -->`の`<div x-show="activeTab === 'stats'">...</div>`ブロック）の直後に追加。**`<template x-for>`の直下の子要素は必ず1つ（`<tbody>`）にすること**（Alpine.jsの制約。複数`<tr>`を直接並べると2番目以降が表示されないバグになる — 既存の「トークン」タブと同じ構造に合わせる）:

```html
          <!-- ===== 請求 ===== -->
          <div x-show="activeTab === 'billing'">
            <div x-show="invoicesLoading" class="py-8 text-center text-gray-400">請求書を取得中...</div>
            <div x-show="!invoicesLoading && invoices.length === 0" class="py-8 text-center text-gray-400 text-sm">
              請求書がありません
            </div>
            <div x-show="!invoicesLoading && invoices.length > 0" class="bg-white rounded shadow-sm border border-gray-200">
              <table class="w-full">
                <thead class="bg-gray-50 border-b border-gray-100">
                  <tr>
                    <th class="w-6 px-4 py-3"></th>
                    <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">対象月</th>
                    <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">状態</th>
                    <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">合計金額</th>
                  </tr>
                </thead>
                <template x-for="inv in invoices" :key="inv.target_year_month">
                  <tbody>
                    <tr class="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                        @click="toggleInvoice(inv.target_year_month)">
                      <td class="px-4 py-3 text-gray-400 text-xs select-none"
                          x-text="expandedInvoiceMonth === inv.target_year_month ? '▾' : '▸'"></td>
                      <td class="px-4 py-3 text-sm font-medium" x-text="inv.target_year_month"></td>
                      <td class="px-4 py-3 text-sm">
                        <span :class="inv.status === 'draft' ? 'text-amber-600' : 'text-green-600'"
                              x-text="inv.status === 'draft' ? '集計中（今月）' : '確定'"></span>
                      </td>
                      <td class="px-4 py-3 text-sm font-bold" x-text="inv.total_amount.toLocaleString() + ' 円'"></td>
                    </tr>
                    <tr x-show="expandedInvoiceMonth === inv.target_year_month" class="bg-blue-50">
                      <td colspan="4" class="px-8 py-3">
                        <div x-show="invoiceDetailLoading" class="text-xs text-gray-400 py-1">読み込み中...</div>
                        <template x-if="!invoiceDetailLoading && invoiceDetail && expandedInvoiceMonth === inv.target_year_month">
                          <div>
                            <p x-show="inv.status === 'draft'" class="text-xs text-amber-600 mb-2">
                              ※このバッチは毎日更新されるため、月末までの推定額です
                            </p>
                            <table class="w-full text-xs">
                              <thead>
                                <tr class="text-gray-500">
                                  <th class="text-left py-1 pr-6 font-medium">項目</th>
                                  <th class="text-left py-1 pr-6 font-medium">数量</th>
                                  <th class="text-left py-1 pr-6 font-medium">単価</th>
                                  <th class="text-left py-1 font-medium">小計</th>
                                </tr>
                              </thead>
                              <tbody>
                                <template x-for="li in invoiceDetail.line_items" :key="li.item_key">
                                  <tr class="border-t border-blue-100">
                                    <td class="py-1.5 pr-6 font-medium text-gray-700" x-text="invoiceItemLabel(li.item_key)"></td>
                                    <td class="py-1.5 pr-6 text-gray-600" x-text="li.quantity.toLocaleString()"></td>
                                    <td class="py-1.5 pr-6 text-gray-500" x-text="li.unit_price"></td>
                                    <td class="py-1.5 text-gray-800" x-text="li.amount.toLocaleString() + ' 円'"></td>
                                  </tr>
                                </template>
                              </tbody>
                            </table>
                            <div class="flex justify-end gap-6 mt-2 text-xs text-gray-600">
                              <span>小計: <b x-text="invoiceDetail.subtotal.toLocaleString() + ' 円'"></b></span>
                              <span>消費税: <b x-text="invoiceDetail.tax_amount.toLocaleString() + ' 円'"></b></span>
                              <span>合計: <b x-text="invoiceDetail.total_amount.toLocaleString() + ' 円'"></b></span>
                            </div>
                          </div>
                        </template>
                      </td>
                    </tr>
                  </tbody>
                </template>
              </table>
            </div>
          </div>
```

- [ ] **Step 6: 手動確認**

1. core-apiとadmin-uiを起動する（既存の開発環境起動手順に従う）
2. テナントポータルにログインし、「請求」タブをクリックする
3. `run_monthly_billing_batch()`が1度も実行されていないテナントでは「請求書がありません」が表示されることを確認する
4. Python REPLか一時的なスクリプトから`from app.services.billing_batch import run_monthly_billing_batch; run_monthly_billing_batch()`を実行し、DBに`billing_unit_prices`が1件も無い状態でも単価0円扱いで請求書が作成されることを確認する（`app.services.billing.calculate_invoice`のdocstring参照）
5. 再度「請求」タブを開き、当月分が「集計中（今月）」として表示され、行をクリックすると明細が展開されることを確認する
6. PF管理者側で該当テナントに単価を設定後、翌月分の`effective_from`を待たずに現状の単価（未設定なら0円）で計算されていることを確認する

- [ ] **Step 7: Commit**

```bash
git add admin-ui/tenant-portal.html
git commit -m "feat(billing): テナントポータルに請求タブを追加"
```

---

## 完了後の確認（フルテストスイート）

全タスク完了後、影響範囲のテストを一括実行して既存テストを壊していないか確認する:

```bash
cd core-api
export POSTGRES_DSN="postgresql://test:test@localhost:5432/test"
python -m pytest tests/test_billing_usage.py tests/test_billing_batch.py tests/test_billing_api.py tests/test_tenant_portal_billing.py tests/test_billing_calc.py tests/test_billing_prices.py tests/test_billing_models.py tests/test_billing_schemas.py tests/test_stats.py -v
```

Expected: 全件PASS。
