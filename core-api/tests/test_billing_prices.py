from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from app.services.billing import (
    InvalidEffectiveDateError,
    get_effective_unit_prices,
    set_unit_price,
)


def _price_row(item_key, unit_price, effective_from):
    row = MagicMock()
    row.item_key = item_key
    row.unit_price = unit_price
    row.effective_from = effective_from
    return row


def test_get_effective_unit_prices_picks_latest_row_per_item():
    mock_db = MagicMock()
    # サービス側は item_key 昇順・effective_from 降順で取得する想定なので、
    # モックにもその順序で渡す（同じ item_key は新しい effective_from が先）。
    rows = [
        _price_row("base_fee", Decimal("6000"), date(2026, 8, 1)),
        _price_row("base_fee", Decimal("5000"), date(2026, 6, 1)),
        _price_row("data_points", Decimal("0.02"), date(2026, 7, 1)),
    ]
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

    result = get_effective_unit_prices(mock_db, "tenant-1", 2026, 9)

    assert result == {"base_fee": Decimal("6000"), "data_points": Decimal("0.02")}


def test_get_effective_unit_prices_empty_when_no_rows():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    result = get_effective_unit_prices(mock_db, "tenant-1", 2026, 9)
    assert result == {}


def test_set_unit_price_rejects_non_first_of_month():
    mock_db = MagicMock()
    with pytest.raises(InvalidEffectiveDateError):
        set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("5000"), date(2099, 1, 15))


def test_set_unit_price_rejects_current_month_or_past():
    mock_db = MagicMock()
    current_month_start = date.today().replace(day=1)
    with pytest.raises(InvalidEffectiveDateError):
        set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("5000"), current_month_start)


def test_set_unit_price_rejects_unknown_item_key():
    mock_db = MagicMock()
    with pytest.raises(ValueError):
        set_unit_price(mock_db, "tenant-1", "not_a_real_key", Decimal("100"), date(2099, 1, 1))


def test_set_unit_price_creates_new_row_for_future_month():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("5000"), date(2099, 1, 1))

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.tenant_id == "tenant-1"
    assert added.item_key == "base_fee"
    assert added.unit_price == Decimal("5000")
    assert added.effective_from == date(2099, 1, 1)
    mock_db.commit.assert_called_once()


def test_set_unit_price_updates_existing_row_for_same_month():
    mock_db = MagicMock()
    existing = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = existing

    set_unit_price(mock_db, "tenant-1", "base_fee", Decimal("7000"), date(2099, 1, 1))

    assert existing.unit_price == Decimal("7000")
    mock_db.add.assert_not_called()
    mock_db.commit.assert_called_once()
