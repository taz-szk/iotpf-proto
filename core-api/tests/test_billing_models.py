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
