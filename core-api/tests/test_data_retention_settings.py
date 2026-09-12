from unittest.mock import MagicMock
import pytest

from app.models.billing import BillingSettings
from app.services.billing import (
    DEFAULT_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    InvalidUnitPriceError,
    get_default_retention_days,
    get_effective_retention_days,
    set_default_retention_days,
    validate_retention_days,
)


def test_default_retention_days_constant_is_365():
    assert DEFAULT_RETENTION_DAYS == 365


def test_min_retention_days_constant_is_60():
    assert MIN_RETENTION_DAYS == 60


def test_get_default_retention_days_returns_configured_value():
    row = MagicMock(spec=BillingSettings)
    row.default_retention_days = 180
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_default_retention_days(mock_db) == 180


def test_get_default_retention_days_falls_back_when_no_row():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_default_retention_days(mock_db) == DEFAULT_RETENTION_DAYS


def test_set_default_retention_days_updates_existing_row():
    row = MagicMock(spec=BillingSettings)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    set_default_retention_days(mock_db, 180)
    assert row.default_retention_days == 180
    mock_db.add.assert_not_called()
    mock_db.commit.assert_called_once()


def test_set_default_retention_days_creates_row_when_missing():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    set_default_retention_days(mock_db, 180)
    assert mock_db.add.called
    added = mock_db.add.call_args[0][0]
    assert added.id == 1
    assert added.default_retention_days == 180
    mock_db.commit.assert_called_once()


def test_get_effective_retention_days_uses_tenant_override():
    tenant = MagicMock(data_retention_days=90)
    mock_db = MagicMock()
    assert get_effective_retention_days(mock_db, tenant) == 90
    mock_db.query.assert_not_called()


def test_get_effective_retention_days_falls_back_to_default():
    tenant = MagicMock(data_retention_days=None)
    default_row = MagicMock(spec=BillingSettings)
    default_row.default_retention_days = 365
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = default_row
    assert get_effective_retention_days(mock_db, tenant) == 365


def test_validate_retention_days_accepts_valid_value():
    assert validate_retention_days("180") == 180


def test_validate_retention_days_accepts_exact_minimum():
    assert validate_retention_days("60") == 60


def test_validate_retention_days_rejects_below_minimum():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("59")


def test_validate_retention_days_rejects_empty_string():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("")


def test_validate_retention_days_rejects_non_integer():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("not-a-number")


def test_validate_retention_days_rejects_decimal():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("180.5")


def test_validate_retention_days_accepts_large_value_no_upper_bound():
    assert validate_retention_days("36500") == 36500


def test_validate_retention_days_rejects_value_exceeding_integer_max():
    with pytest.raises(InvalidUnitPriceError):
        validate_retention_days("2147483648")
    assert validate_retention_days("2147483647") == 2147483647


def test_get_effective_retention_days_clamps_below_minimum_floor():
    tenant = MagicMock(data_retention_days=10)
    mock_db = MagicMock()
    assert get_effective_retention_days(mock_db, tenant) == MIN_RETENTION_DAYS
