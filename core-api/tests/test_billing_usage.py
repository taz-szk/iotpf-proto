from unittest.mock import patch, MagicMock
from app.services.billing_usage import (
    _month_range_rfc3339,
    _count_influxdb_points_for_month,
    _count_total_retained_points,
    _count_retired_retained_points,
    _count_retired_devices,
    _count_registered_devices,
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


def test_count_total_retained_points_has_no_device_name_filter():
    """否定regex(not (...))は大量データで遅くタイムアウトしうる(実機で確認済み)ため、
    全体件数は device_nameで絞り込まず数える。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,500\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_total_retained_points("org-1", "tok", 90)
    assert result == 500
    query = mock_httpx.post.call_args.kwargs["json"]["query"]
    assert "range(start: -90d)" in query
    assert "device_name" not in query


def test_count_total_retained_points_returns_zero_on_error_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_total_retained_points("org-1", "tok", 90)
    assert result == 0


def test_count_retired_retained_points_uses_positive_regex_only():
    """肯定regex(=~ /^Del_/)のみを使い、否定(not)は使わない(タイムアウト対策)。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,42\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_retired_retained_points("org-1", "tok", 90)
    assert result == 42
    query = mock_httpx.post.call_args.kwargs["json"]["query"]
    assert "range(start: -90d)" in query
    assert 'r.device_name =~ /^Del_/' in query
    assert 'not (' not in query


def test_count_retired_retained_points_returns_zero_on_error_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_retired_retained_points("org-1", "tok", 90)
    assert result == 0


def test_count_retired_devices_parses_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,3\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_retired_devices("org-1", "tok", 90)
    assert result == 3
    query = mock_httpx.post.call_args.kwargs["json"]["query"]
    assert 'r._measurement == "device_deleted"' in query
    assert 'r.device_name =~ /^Del_/' in query


def test_count_retired_devices_returns_zero_on_error_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_retired_devices("org-1", "tok", 90)
    assert result == 0


def test_count_registered_devices_counts_devices_table_rows():
    """「デバイス一覧」画面の表示件数(devicesテーブルの行数)と一致する、重複のないスナップショット。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = 5
    result = _count_registered_devices(mock_db, "tenant_x")
    assert result == 5
    sql_text = str(mock_db.execute.call_args[0][0])
    assert "tenant_x" in sql_text
    assert "devices" in sql_text


def test_count_registered_devices_returns_zero_when_none():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = None
    result = _count_registered_devices(mock_db, "tenant_x")
    assert result == 0


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
         patch("app.services.billing_usage._count_total_retained_points", return_value=320), \
         patch("app.services.billing_usage._count_retired_retained_points", return_value=20), \
         patch("app.services.billing_usage._count_retired_devices", return_value=2), \
         patch("app.services.billing_usage._count_registered_devices", return_value=5), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=2), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(40, False)), \
         patch("app.services.billing_usage.get_effective_retention_days", return_value=365):
        usage = aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    assert usage == {
        "base_fee": 1,
        "data_points": 100,
        "retained_data_points": 300,  # 320(全体) - 20(削除済み)
        "retired_data_points": 20,
        "retired_device_count": 2,
        "device_count": 5,
        "provisionable_devices": 40,
        "alert_events": 2,
    }


def test_aggregate_monthly_usage_retained_data_points_never_negative():
    """全体件数と削除済み件数は別々のスナップショットクエリなので、タイミングのずれで
    削除済み > 全体になっても稼働中分が負の数にならないようにする。"""
    mock_db = MagicMock()
    with patch("app.services.billing_usage._count_influxdb_points_for_month", return_value=10), \
         patch("app.services.billing_usage._count_total_retained_points", return_value=5), \
         patch("app.services.billing_usage._count_retired_retained_points", return_value=8), \
         patch("app.services.billing_usage._count_retired_devices", return_value=1), \
         patch("app.services.billing_usage._count_registered_devices", return_value=1), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=0), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(1, False)), \
         patch("app.services.billing_usage.get_effective_retention_days", return_value=60):
        usage = aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    assert usage["retained_data_points"] == 0
    assert usage["retired_data_points"] == 8


def test_aggregate_monthly_usage_passes_effective_retention_days_to_retained_points():
    """retained/retired_data_pointsの集計にはテナントの実効保持日数を使う必要がある。"""
    mock_db = MagicMock()
    with patch("app.services.billing_usage._count_influxdb_points_for_month", return_value=10), \
         patch("app.services.billing_usage._count_total_retained_points", return_value=0) as mock_total, \
         patch("app.services.billing_usage._count_retired_retained_points", return_value=0) as mock_retired, \
         patch("app.services.billing_usage._count_retired_devices", return_value=0), \
         patch("app.services.billing_usage._count_registered_devices", return_value=1), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=0), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(1, False)), \
         patch("app.services.billing_usage.get_effective_retention_days", return_value=730):
        aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    assert mock_total.call_args.args[2] == 730
    assert mock_retired.call_args.args[2] == 730
