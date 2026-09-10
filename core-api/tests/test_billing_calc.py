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
