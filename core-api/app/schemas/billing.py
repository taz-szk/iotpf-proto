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


class InvoiceOut(BaseModel):
    target_year_month: str
    status: str
    subtotal: int
    tax_amount: int
    total_amount: int
    correction_count: int = 0
