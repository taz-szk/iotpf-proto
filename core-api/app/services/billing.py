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
