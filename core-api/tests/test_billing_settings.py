from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.billing import BillingSettings
from app.services.billing import (
    DEFAULT_TAX_RATE,
    InvalidUnitPriceError,
    get_tax_rate,
    set_tax_rate,
    validate_tax_rate,
)


def test_get_tax_rate_returns_configured_value():
    mock_db = MagicMock()
    row = MagicMock(spec=BillingSettings)
    row.tax_rate = Decimal("0.08")
    mock_db.query.return_value.filter.return_value.first.return_value = row
    assert get_tax_rate(mock_db) == Decimal("0.08")


def test_get_tax_rate_falls_back_to_default_when_no_row():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    assert get_tax_rate(mock_db) == DEFAULT_TAX_RATE


def test_set_tax_rate_updates_existing_row():
    mock_db = MagicMock()
    row = MagicMock(spec=BillingSettings)
    mock_db.query.return_value.filter.return_value.first.return_value = row
    set_tax_rate(mock_db, Decimal("0.08"))
    assert row.tax_rate == Decimal("0.08")
    mock_db.add.assert_not_called()
    mock_db.commit.assert_called_once()


def test_set_tax_rate_creates_row_when_missing():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    set_tax_rate(mock_db, Decimal("0.08"))
    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.id == 1
    assert added.tax_rate == Decimal("0.08")
    mock_db.commit.assert_called_once()


def test_validate_tax_rate_accepts_valid_values():
    assert validate_tax_rate("0.10") == Decimal("0.10")
    assert validate_tax_rate("0") == Decimal("0")
    assert validate_tax_rate("1") == Decimal("1")


def test_validate_tax_rate_rejects_negative():
    with pytest.raises(InvalidUnitPriceError):
        validate_tax_rate("-0.01")


def test_validate_tax_rate_rejects_above_one():
    with pytest.raises(InvalidUnitPriceError):
        validate_tax_rate("1.01")


def test_validate_tax_rate_rejects_malformed_string():
    with pytest.raises(InvalidUnitPriceError):
        validate_tax_rate("not-a-number")
