from datetime import date
import pytest
from pydantic import ValidationError
from app.schemas.billing import UnitPriceSet, UnitPriceOut


def test_unit_price_set_accepts_valid_item_key():
    body = UnitPriceSet(item_key="base_fee", unit_price="5000", effective_from=date(2099, 1, 1))
    assert body.item_key == "base_fee"
    assert body.unit_price == "5000"


def test_unit_price_set_rejects_unknown_item_key():
    with pytest.raises(ValidationError):
        UnitPriceSet(item_key="not_a_real_key", unit_price="5000", effective_from=date(2099, 1, 1))


def test_unit_price_out_serializes_as_string_price():
    out = UnitPriceOut(item_key="data_points", unit_price="0.01", effective_from=date(2026, 9, 1))
    assert out.model_dump()["unit_price"] == "0.01"
