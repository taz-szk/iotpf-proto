from unittest.mock import MagicMock
import pytest

from app.services.billing import (
    InvalidUnitPriceError,
    get_default_bill_shock_threshold,
    get_effective_bill_shock_threshold,
    set_default_bill_shock_threshold,
    validate_bill_shock_threshold,
)


def test_get_default_bill_shock_threshold_returns_value():
    row = MagicMock(default_bill_shock_threshold_amount=50000)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_default_bill_shock_threshold(mock_db) == 50000


def test_get_default_bill_shock_threshold_none_when_no_row():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_default_bill_shock_threshold(mock_db) is None


def test_get_default_bill_shock_threshold_none_when_column_unset():
    row = MagicMock(default_bill_shock_threshold_amount=None)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_default_bill_shock_threshold(mock_db) is None


def test_set_default_bill_shock_threshold_updates_existing_row():
    row = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    set_default_bill_shock_threshold(mock_db, 60000)
    assert row.default_bill_shock_threshold_amount == 60000
    mock_db.commit.assert_called_once()


def test_set_default_bill_shock_threshold_creates_row_when_missing():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    set_default_bill_shock_threshold(mock_db, 60000)
    assert mock_db.add.called
    added = mock_db.add.call_args[0][0]
    assert added.id == 1
    assert added.default_bill_shock_threshold_amount == 60000
    mock_db.commit.assert_called_once()


def test_get_effective_bill_shock_threshold_uses_tenant_override():
    tenant = MagicMock(bill_shock_threshold_amount=12345)
    mock_db = MagicMock()
    assert get_effective_bill_shock_threshold(mock_db, tenant) == 12345
    mock_db.query.assert_not_called()


def test_get_effective_bill_shock_threshold_falls_back_to_default():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    default_row = MagicMock(default_bill_shock_threshold_amount=99999)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = default_row
    assert get_effective_bill_shock_threshold(mock_db, tenant) == 99999


def test_get_effective_bill_shock_threshold_none_when_both_unset():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_effective_bill_shock_threshold(mock_db, tenant) is None


def test_validate_bill_shock_threshold_accepts_valid_integer():
    assert validate_bill_shock_threshold("50000") == 50000


def test_validate_bill_shock_threshold_empty_string_means_unset():
    assert validate_bill_shock_threshold("") is None


def test_validate_bill_shock_threshold_rejects_negative():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("-1")


def test_validate_bill_shock_threshold_rejects_non_integer():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("not-a-number")


def test_validate_bill_shock_threshold_rejects_decimal():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("50000.5")


def test_validate_bill_shock_threshold_rejects_value_exceeding_integer_max():
    with pytest.raises(InvalidUnitPriceError):
        validate_bill_shock_threshold("2147483648")
    assert validate_bill_shock_threshold("2147483647") == 2147483647
