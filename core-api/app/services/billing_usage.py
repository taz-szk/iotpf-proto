from datetime import datetime, timezone
import httpx

from app.config import settings
from app.routers.stats import _parse_influx_csv_scalar, _calc_provisionable_devices


def _month_range_rfc3339(year: int, month: int) -> tuple[str, str]:
    """対象年月の[月初, 翌月初)をRFC3339(UTC)の文字列ペアで返す。"""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        stop = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        stop = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start.strftime('%Y-%m-%dT%H:%M:%SZ'), stop.strftime('%Y-%m-%dT%H:%M:%SZ')


def _count_influxdb_points_for_month(influxdb_org_id: str, token: str, year: int, month: int) -> int:
    """対象年月に処理・保存されたテレメトリの総数。"""
    start, stop = _month_range_rfc3339(year, month)
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {start}, stop: {stop})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> group()\n'
        '  |> count()\n'
        '  |> sum()\n'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={influxdb_org_id}",
            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
            json={"query": query, "type": "flux"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return 0
        return _parse_influx_csv_scalar(resp.text)
    except Exception:
        return 0


def _count_unique_devices_for_month(influxdb_org_id: str, token: str, year: int, month: int) -> int:
    """対象年月にテレメトリを送信したユニークdevice_name数。"""
    start, stop = _month_range_rfc3339(year, month)
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {start}, stop: {stop})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> keep(columns: ["device_name"])\n'
        '  |> group()\n'
        '  |> distinct(column: "device_name")\n'
        '  |> group()\n'
        '  |> count()\n'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={influxdb_org_id}",
            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
            json={"query": query, "type": "flux"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return 0
        return _parse_influx_csv_scalar(resp.text)
    except Exception:
        return 0


def _count_alert_events_for_month(db, schema: str, year: int, month: int) -> int:
    """対象年月に発報されたアラート総数。"""
    from sqlalchemy import text
    start, stop = _month_range_rfc3339(year, month)
    result = db.execute(text(f'''
        SELECT COUNT(*) FROM "{schema}".alert_events
        WHERE triggered_at >= :start AND triggered_at < :stop
    '''), {"start": start, "stop": stop}).scalar()
    return result or 0


def aggregate_monthly_usage(
    db, tenant_id: str, schema: str, influxdb_org_id: str, influxdb_token: str,
    year: int, month: int,
) -> dict[str, int]:
    """対象年月の利用量を集計し、app.services.billing.calculate_invoice()に渡せる
    usage dictを返す。provisionable_devicesは「現在時点」のスナップショットなので、
    finalized済みの月に対しては呼び出し側が絶対に呼ばないこと。"""
    provisionable_devices, _has_unlimited = _calc_provisionable_devices(db, tenant_id, schema)
    return {
        "base_fee": 1,
        "data_points": _count_influxdb_points_for_month(influxdb_org_id, influxdb_token, year, month),
        "device_count": _count_unique_devices_for_month(influxdb_org_id, influxdb_token, year, month),
        "provisionable_devices": provisionable_devices,
        "alert_events": _count_alert_events_for_month(db, schema, year, month),
    }
