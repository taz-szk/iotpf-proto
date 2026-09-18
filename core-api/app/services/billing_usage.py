from datetime import datetime, timezone
import httpx

from app.config import settings
from app.models.public import Tenant
from app.routers.stats import _parse_influx_csv_scalar, _calc_provisionable_devices
from app.services.billing import get_effective_retention_days


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


def _count_influxdb_retained_points(influxdb_org_id: str, token: str, retention_days: int, archived: bool) -> int:
    """現在(スナップショット時点)保持されているテレメトリ点数。
    archived=Falseなら現役デバイス(device_nameがDel_接頭辞でないもの)、
    archived=Trueなら退役デバイス(Del_接頭辞のアーカイブ)のみを対象にする。"""
    name_filter = 'r.device_name =~ /^Del_/' if archived else 'not (r.device_name =~ /^Del_/)'
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: -{retention_days}d)\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        f'  |> filter(fn: (r) => {name_filter})\n'
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


def _count_retired_devices(influxdb_org_id: str, token: str, retention_days: int) -> int:
    """現在アーカイブされている(Del_接頭辞の)退役デバイスの台数(スナップショット時点)。"""
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: -{retention_days}d)\n'
        '  |> filter(fn: (r) => r._measurement == "device_deleted")\n'
        '  |> filter(fn: (r) => r.device_name =~ /^Del_/)\n'
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


def _count_registered_devices(db, schema: str) -> int:
    """現在登録されている(削除されていない)デバイスの台数(devicesテーブルの行数、重複なし)。
    「デバイス一覧」画面の表示件数と一致する、現在時点のスナップショット。"""
    from sqlalchemy import text
    result = db.execute(text(f'SELECT COUNT(*) FROM "{schema}".devices')).scalar()
    return result or 0


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
    usage dictを返す。provisionable_devices・retained_data_points・retired_data_points・
    retired_device_count・device_countは「現在時点」のスナップショットなので、
    finalized済みの月に対しては呼び出し側が絶対に呼ばないこと。
    retained_data_points/retired_data_pointsは「今まさにInfluxDBに保持されている
    テレメトリ点数」を、device_nameがDel_接頭辞(退役デバイスのアーカイブ)かどうかで
    分けたもの。退役デバイスは強制削除せずリテンションで自然に消えるまで保持され続けるため、
    現役分とは別単価で課金できるようにするための指標。
    device_count/retired_device_countも同様に「現在の登録デバイス数」「現在の退役
    (アーカイブ)デバイス数」という重複のないスナップショットで、対になっている。"""
    provisionable_devices, _has_unlimited = _calc_provisionable_devices(db, tenant_id, schema)
    data_points = _count_influxdb_points_for_month(influxdb_org_id, influxdb_token, year, month)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    retention_days = get_effective_retention_days(db, tenant)
    return {
        "base_fee": 1,
        "data_points": data_points,
        "retained_data_points": _count_influxdb_retained_points(influxdb_org_id, influxdb_token, retention_days, archived=False),
        "retired_data_points": _count_influxdb_retained_points(influxdb_org_id, influxdb_token, retention_days, archived=True),
        "retired_device_count": _count_retired_devices(influxdb_org_id, influxdb_token, retention_days),
        "device_count": _count_registered_devices(db, schema),
        "provisionable_devices": provisionable_devices,
        "alert_events": _count_alert_events_for_month(db, schema, year, month),
    }
