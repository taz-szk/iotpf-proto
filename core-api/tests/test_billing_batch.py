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
