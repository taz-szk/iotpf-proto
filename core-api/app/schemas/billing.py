from datetime import date
from typing import Literal
from pydantic import BaseModel, Field

from app.services.billing import ITEM_KEYS

# 課金項目の定義(ITEM_KEYS)と常に一致させる。ここを手書きすると新しい項目の単価が設定できなくなる
ItemKey = Literal[ITEM_KEYS]


class UnitPriceSet(BaseModel):
    item_key: ItemKey
    unit_price: str = Field(description="10進数の文字列（例: \"0.015\"）。Decimalとして解釈される")
    effective_from: date


class UnitPriceOut(BaseModel):
    item_key: str
    unit_price: str
    effective_from: date


class InvoiceOut(BaseModel):
    target_year_month: str
    status: str
    subtotal: int
    tax_amount: int
    total_amount: int
    correction_count: int = 0


class BillShockThresholdOut(BaseModel):
    threshold_amount: str | None
    is_default: bool


class BillShockThresholdSet(BaseModel):
    threshold_amount: str | None = None
