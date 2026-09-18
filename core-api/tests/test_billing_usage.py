from unittest.mock import patch, MagicMock
from app.services.billing_usage import (
    _month_range_rfc3339,
    _count_influxdb_points_for_month,
    _count_influxdb_retained_points,
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


def test_count_influxdb_retained_points_active_excludes_del_prefixed():
    """archived=Falseの場合、device_nameがDel_接頭辞のものを除外するクエリになる。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,500\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_retained_points("org-1", "tok", 90, archived=False)
    assert result == 500
    query = mock_httpx.post.call_args.kwargs["json"]["query"]
    assert "range(start: -90d)" in query
    assert 'not (r.device_name =~ /^Del_/)' in query


def test_count_influxdb_retained_points_archived_only_matches_del_prefixed():
    """archived=Trueの場合、device_nameがDel_接頭辞のものだけを対象にするクエリになる。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",result,table,_value\n,_result,0,42\n"
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_retained_points("org-1", "tok", 90, archived=True)
    assert result == 42
    query = mock_httpx.post.call_args.kwargs["json"]["query"]
    assert "range(start: -90d)" in query
    assert 'r.device_name =~ /^Del_/' in query
    assert 'not (' not in query


def test_count_influxdb_retained_points_returns_zero_on_error_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("app.services.billing_usage.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        result = _count_influxdb_retained_points("org-1", "tok", 90, archived=False)
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
         patch("app.services.billing_usage._count_influxdb_retained_points", side_effect=[300, 20]), \
         patch("app.services.billing_usage._count_retired_devices", return_value=2), \
         patch("app.services.billing_usage._count_registered_devices", return_value=5), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=2), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(40, False)), \
         patch("app.services.billing_usage.get_effective_retention_days", return_value=365):
        usage = aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    assert usage == {
        "base_fee": 1,
        "data_points": 100,
        "retained_data_points": 300,
        "retired_data_points": 20,
        "retired_device_count": 2,
        "device_count": 5,
        "provisionable_devices": 40,
        "alert_events": 2,
    }


def test_aggregate_monthly_usage_passes_effective_retention_days_to_retained_points():
    """retained/retired_data_pointsの集計にはテナントの実効保持日数を使う必要がある。"""
    mock_db = MagicMock()
    with patch("app.services.billing_usage._count_influxdb_points_for_month", return_value=10), \
         patch("app.services.billing_usage._count_influxdb_retained_points", return_value=0) as mock_retained, \
         patch("app.services.billing_usage._count_retired_devices", return_value=0), \
         patch("app.services.billing_usage._count_registered_devices", return_value=1), \
         patch("app.services.billing_usage._count_alert_events_for_month", return_value=0), \
         patch("app.services.billing_usage._calc_provisionable_devices", return_value=(1, False)), \
         patch("app.services.billing_usage.get_effective_retention_days", return_value=730):
        aggregate_monthly_usage(mock_db, "tenant-1", "tenant_x", "org-1", "tok", 2026, 9)
    for call in mock_retained.call_args_list:
        assert call.args[2] == 730
    archived_flags = {call.kwargs.get("archived", call.args[3] if len(call.args) > 3 else None) for call in mock_retained.call_args_list}
    assert archived_flags == {True, False}
