from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from app.models.billing import BillingDefaultUnitPrice
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_unit_prices,
    seed_tenant_default_prices,
    set_default_unit_prices,
    validate_unit_price,
)


def _default_row(item_key, unit_price):
    row = MagicMock()
    row.item_key = item_key
    row.unit_price = unit_price
    return row


def test_get_default_unit_prices_returns_dict():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = [
        _default_row("base_fee", Decimal("5000")),
        _default_row("data_points", Decimal("0.01")),
    ]
    result = get_default_unit_prices(mock_db)
    assert result == {"base_fee": Decimal("5000"), "data_points": Decimal("0.01")}


def test_get_default_unit_prices_empty_when_none_set():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = []
    assert get_default_unit_prices(mock_db) == {}


def test_set_default_unit_prices_replaces_all():
    mock_db = MagicMock()
    set_default_unit_prices(mock_db, {"base_fee": Decimal("5000"), "data_points": Decimal("0.01")})
    mock_db.query.assert_called_once_with(BillingDefaultUnitPrice)
    mock_db.query.return_value.delete.assert_called_once()
    assert mock_db.add.call_count == 2
    mock_db.commit.assert_called_once()


def test_seed_tenant_default_prices_copies_all_defaults():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = [
        _default_row("base_fee", Decimal("5000")),
        _default_row("data_points", Decimal("0.01")),
    ]
    seed_tenant_default_prices(mock_db, "tenant-1")
    assert mock_db.add.call_count == 2
    added = [call.args[0] for call in mock_db.add.call_args_list]
    assert all(a.tenant_id == "tenant-1" for a in added)
    assert all(a.effective_from == date.today().replace(day=1) for a in added)
    assert {a.item_key for a in added} == {"base_fee", "data_points"}


def test_seed_tenant_default_prices_noop_when_no_defaults():
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = []
    seed_tenant_default_prices(mock_db, "tenant-1")
    mock_db.add.assert_not_called()


def test_validate_unit_price_accepts_valid_decimal():
    assert validate_unit_price("5000") == Decimal("5000")
    assert validate_unit_price("0.0100") == Decimal("0.0100")


def test_validate_unit_price_rejects_malformed_string():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("not-a-number")


def test_validate_unit_price_rejects_negative():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("-1")


def test_validate_unit_price_rejects_exceeding_maximum():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("100000000")


def test_validate_unit_price_rejects_too_many_decimal_places():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("1.00001")


def test_validate_unit_price_rejects_nan():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("NaN")


def test_validate_unit_price_rejects_infinity():
    with pytest.raises(InvalidUnitPriceError):
        validate_unit_price("Infinity")
