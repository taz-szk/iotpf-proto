from unittest.mock import patch, MagicMock
from app.services.billing_usage import (
    _month_range_rfc3339,
    _count_influxdb_points_for_month,
    _count_unique_devices_for_month,
    _count_alert_events_for_month,
    aggregate_monthly_usage,
)


def test_month_range_rfc3339_normal_month():
    start, stop = _month_range_rfc3339(2026, 9)
    assert start == "2026-09-01T00:00:00Z"
    assert stop == "2026-10-01T00:00:00Z"


def test_month_range_rfc3339_december_rolls_to_next_year():
    start, stop = _month_range_rfc3339(2026, 12)
    assert start == "2026-12-01T00:00:00Z"
    assert stop == "2027-01-01T00:00:00Z"


def test_count_influxdb_points_for_month_parses_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,999\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_points_for_month("org-1", "tok", 2026, 9)
    assert result == 999
    call_kwargs = mock_httpx.post.call_args
    assert "2026-09-01T00:00:00Z" in call_kwargs.kwargs["json"]["query"]
    assert "2026-10-01T00:00:00Z" in call_kwargs.kwargs["json"]["query"]


def test_count_influxdb_points_for_month_returns_zero_on_error_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_points_for_month("org-1", "tok", 2026, 9)
    assert result == 0


def test_count_unique_devices_for_month_parses_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,7\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_unique_devices_for_month("org-1", "tok", 2026, 9)
    assert result == 7


def test_count_alert_events_for_month_queries_target_month():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = 3
    result = _count_alert_events_for_month(mock_db, "tenant_x", 2026, 9)
    assert result == 3
    sql_text = str(mock_db.execute.call_args[0][0])
    assert "alert_events" in sql_text


def test_aggregate_monthly_usage_returns_all_item_keys():
    mock_db = MagicMock()
    with patch("app.services.billing_usage._count_influxdb_points_for_month", return_value=100), \
         patch("app.services.billing_usage._count_unique_devices_for_month", return_value=5), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=2), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(40, False)):
        usage = aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    assert usage == {
        "base_fee": 1,
        "data_points": 100,
        "device_count": 5,
        "provisionable_devices": 40,
        "alert_events": 2,
    }
