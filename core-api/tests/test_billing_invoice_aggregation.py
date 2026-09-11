from decimal import Decimal
from unittest.mock import MagicMock

from app.services.billing import get_invoice_detail_aggregated, list_invoices_aggregated


def test_list_invoices_aggregated_sums_finalized_and_corrected_rows_for_same_month():
    finalized = MagicMock(target_year_month="2026-09", status="finalized",
                           subtotal=100, tax_amount=10, total_amount=110)
    corrected = MagicMock(target_year_month="2026-09", status="corrected",
                           subtotal=20, tax_amount=2, total_amount=22)
    draft = MagicMock(target_year_month="2026-10", status="draft",
                       subtotal=5, tax_amount=0, total_amount=5)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        finalized, corrected, draft,
    ]

    result = list_invoices_aggregated(mock_db, "tenant-1")

    by_month = {r["target_year_month"]: r for r in result}
    sep = by_month["2026-09"]
    assert sep["status"] == "finalized"
    assert sep["subtotal"] == 120
    assert sep["tax_amount"] == 12
    assert sep["total_amount"] == 132
    assert sep["correction_count"] == 1

    oct_ = by_month["2026-10"]
    assert oct_["status"] == "draft"
    assert oct_["total_amount"] == 5
    assert oct_["correction_count"] == 0

    # newest month first
    assert [r["target_year_month"] for r in result] == ["2026-10", "2026-09"]


def test_get_invoice_detail_aggregated_returns_none_when_no_rows():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    assert get_invoice_detail_aggregated(mock_db, "tenant-1", "2026-09") is None


def test_get_invoice_detail_aggregated_sums_line_items_and_lists_corrections():
    finalized = MagicMock(status="finalized", id="inv-f", subtotal=100, tax_amount=10,
                           total_amount=110, finalized_at="2026-09-30T00:00:00Z")
    corrected = MagicMock(status="corrected", id="inv-c", subtotal=20, tax_amount=2,
                           total_amount=22, finalized_at="2026-10-05T00:00:00Z")
    li_f = MagicMock(item_key="data_points", quantity=1000, amount=100, unit_price=Decimal("0.1"))
    li_c = MagicMock(item_key="data_points", quantity=200, amount=20, unit_price=Decimal("0.1"))

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [finalized, corrected]
    mock_db.query.return_value.filter.return_value.all.side_effect = [[li_f], [li_c]]

    result = get_invoice_detail_aggregated(mock_db, "tenant-1", "2026-09")

    assert result["status"] == "finalized"
    assert result["subtotal"] == 120
    assert result["tax_amount"] == 12
    assert result["total_amount"] == 132
    assert result["correction_count"] == 1
    assert len(result["corrections"]) == 1
    assert result["corrections"][0]["delta_total_amount"] == 22

    assert len(result["line_items"]) == 1
    li = result["line_items"][0]
    assert li["item_key"] == "data_points"
    assert li["quantity"] == 1200
    assert li["amount"] == 120
