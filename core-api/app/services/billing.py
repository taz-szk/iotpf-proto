from datetime import date
from decimal import Decimal, ROUND_DOWN

from sqlalchemy.orm import Session

from app.models.billing import BillingUnitPrice

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
