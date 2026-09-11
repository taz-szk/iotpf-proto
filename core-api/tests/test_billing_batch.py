from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
from app.services.billing_batch import (
    _backfill_missing_months,
    _current_target_year_month,
    _finalize_stale_drafts,
    _get_or_create_draft_invoice,
    _months_between_exclusive,
    _replace_line_items,
    run_monthly_billing_batch,
)


def test_current_target_year_month_formats_correctly():
    fixed_now = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)
    with patch("app.services.billing_batch.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        year, month, ym = _current_target_year_month()
    assert (year, month, ym) == (2026, 9, "2026-09")


def test_months_between_exclusive_normal_gap():
    assert _months_between_exclusive("2026-09", "2026-11") == ["2026-10"]


def test_months_between_exclusive_multi_month_gap_crossing_year():
    assert _months_between_exclusive("2026-11", "2027-02") == ["2026-12", "2027-01"]


def test_months_between_exclusive_no_gap():
    assert _months_between_exclusive("2026-09", "2026-10") == []


def test_months_between_exclusive_same_month():
    assert _months_between_exclusive("2026-09", "2026-09") == []


def test_months_between_exclusive_start_after_end_returns_empty():
    assert _months_between_exclusive("2026-11", "2026-09") == []


def test_backfill_missing_months_noop_when_no_existing_invoices():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch("app.services.billing_batch.aggregate_monthly_usage") as mock_aggregate:
        _backfill_missing_months(mock_db, "tenant-1", "tenant_x", "org-1", "tok", "2026-11")

    mock_aggregate.assert_not_called()


def test_backfill_missing_months_noop_when_no_gap():
    mock_db = MagicMock()
    existing = MagicMock(target_year_month="2026-10")
    mock_db.query.return_value.filter.return_value.all.return_value = [existing]

    with patch("app.services.billing_batch.aggregate_monthly_usage") as mock_aggregate:
        _backfill_missing_months(mock_db, "tenant-1", "tenant_x", "org-1", "tok", "2026-11")

    mock_aggregate.assert_not_called()


def test_backfill_missing_months_creates_finalized_invoice_for_gap():
    existing = MagicMock(target_year_month="2026-09")
    latest_invoice = MagicMock(id="invoice-sep")
    old_line_item = MagicMock(item_key="provisionable_devices", quantity=99)

    mock_db = MagicMock()
    # 1st .all(): 既存請求書一覧(target_year_month収集用) 2nd .all(): 直近請求書の明細行
    mock_db.query.return_value.filter.return_value.all.side_effect = [[existing], [old_line_item]]
    mock_db.query.return_value.filter.return_value.first.return_value = latest_invoice

    with patch("app.services.billing_batch.aggregate_monthly_usage",
               return_value={"base_fee": 1, "data_points": 500, "device_count": 2,
                             "provisionable_devices": 0, "alert_events": 3}) as mock_aggregate, \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={}), \
         patch("app.services.billing_batch.get_tax_rate", return_value=Decimal("0.10")), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [{"item_key": "provisionable_devices", "quantity": 99,
                              "unit_price": Decimal("0"), "amount": 0}],
             "subtotal": 100, "tax_amount": 10, "total_amount": 110,
         }) as mock_calc, \
         patch("app.services.billing_batch._replace_line_items") as mock_replace:
        _backfill_missing_months(mock_db, "tenant-1", "tenant_x", "org-1", "tok", "2026-11")

    mock_aggregate.assert_called_once_with(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 10)
    # provisionable_devices must be carried forward from the latest known invoice, not the fresh snapshot
    passed_usage = mock_calc.call_args[0][0]
    assert passed_usage["provisionable_devices"] == 99
    mock_db.add.assert_called_once()
    added_invoice = mock_db.add.call_args[0][0]
    assert added_invoice.target_year_month == "2026-10"
    assert added_invoice.status == "finalized"
    assert added_invoice.total_amount == 110
    mock_replace.assert_called_once()


def test_finalize_stale_drafts_recomputes_before_finalizing():
    old_draft = MagicMock(status="draft", target_year_month="2026-08", id="invoice-1")
    old_line_item = MagicMock(item_key="provisionable_devices", quantity=42)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.side_effect = [[old_draft], [old_line_item]]

    with patch("app.services.billing_batch.aggregate_monthly_usage",
               return_value={"base_fee": 1, "data_points": 999, "device_count": 3,
                             "provisionable_devices": 0, "alert_events": 5}) as mock_aggregate, \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={}), \
         patch("app.services.billing_batch.get_tax_rate", return_value=Decimal("0.08")) as mock_tax_rate, \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [{"item_key": "provisionable_devices", "quantity": 42,
                              "unit_price": Decimal("0"), "amount": 0}],
             "subtotal": 100, "tax_amount": 10, "total_amount": 110,
         }) as mock_calc, \
         patch("app.services.billing_batch._replace_line_items") as mock_replace:
        _finalize_stale_drafts(mock_db, "tenant-1", "tenant_x", "org-1", "tok", "2026-09")

    mock_aggregate.assert_called_once_with(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 8)
    mock_tax_rate.assert_called_once_with(mock_db)
    # provisionable_devices must be overridden with the preserved value, not the freshly-aggregated one
    passed_usage = mock_calc.call_args[0][0]
    assert passed_usage["provisionable_devices"] == 42
    assert mock_calc.call_args[0][2] == Decimal("0.08")
    assert old_draft.status == "finalized"
    assert old_draft.finalized_at is not None
    assert old_draft.subtotal == 100
    assert old_draft.total_amount == 110
    mock_replace.assert_called_once_with(mock_db, "invoice-1", mock_calc.return_value["line_items"])


def test_finalize_stale_drafts_noop_when_nothing_stale():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch("app.services.billing_batch.aggregate_monthly_usage") as mock_aggregate:
        _finalize_stale_drafts(mock_db, "tenant-1", "tenant_x", "org-1", "tok", "2026-09")

    mock_aggregate.assert_not_called()
    mock_db.commit.assert_not_called()


def test_finalize_stale_drafts_only_finalizes_strictly_past_months():
    """target_year_month が current と同じ、または未来（時刻ずれ等）の draft は
    finalizeの対象に含めてはならない -> `<` 比較で除外されることをフィルタ呼び出しで確認する。"""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch("app.services.billing_batch.aggregate_monthly_usage"):
        _finalize_stale_drafts(mock_db, "tenant-1", "tenant_x", "org-1", "tok", "2026-09")

    filter_args = mock_db.query.return_value.filter.call_args[0]
    assert any("target_year_month" in str(arg) and "<" in str(arg) for arg in filter_args)


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
    filter_args = mock_db.query.return_value.filter.call_args[0]
    # str(arg) renders SQLAlchemy binary expressions without bound values (e.g.
    # "billing_invoices.tenant_id = :tenant_id_1"), so also check the bound
    # parameter's actual value directly to pin the real filter predicate.
    assert any(
        "tenant-1" in str(arg) or getattr(getattr(arg, "right", None), "value", None) == "tenant-1"
        for arg in filter_args
    )


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
         patch("app.services.billing_batch.get_tax_rate", return_value=Decimal("0.08")), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [{"item_key": "base_fee", "quantity": 1, "unit_price": Decimal("5000"), "amount": 5000}],
             "subtotal": 5000, "tax_amount": 500, "total_amount": 5500,
         }) as mock_calc:
        results = run_monthly_billing_batch()

    assert results == [{"tenant_id": "tenant-1", "status": "ok", "total_amount": 5500}]
    assert draft_invoice.subtotal == 5000
    assert draft_invoice.total_amount == 5500
    assert mock_calc.call_args[0][2] == Decimal("0.08")


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
         patch("app.services.billing_batch.aggregate_monthly_usage", return_value={"base_fee": 1}) as mock_aggregate, \
         patch("app.services.billing_batch.get_effective_unit_prices", return_value={}), \
         patch("app.services.billing_batch.calculate_invoice", return_value={
             "line_items": [], "subtotal": 0, "tax_amount": 0, "total_amount": 0,
         }):
        results = run_monthly_billing_batch()

    assert results == [{"tenant_id": "tenant-1", "status": "skipped_not_draft"}]
    mock_aggregate.assert_not_called()
