from influxdb_client import InfluxDBClient
from app.config import settings
import re

BUCKET_NAME = "telemetry"


def _safe_id(value: str) -> bool:
    return bool(value and re.fullmatch(r'[\w\-\.]+', value))


def _flux_values_by_device(
    org_id: str,
    device_id: str | None,
    sensor_key: str,
    window_sec: int,
) -> dict[str, list[float]]:
    """Return {device_id: [value_newest_first, ...]} for matching telemetry."""
    if not _safe_id(sensor_key):
        return {}

    device_filter = ""
    if device_id:
        if not _safe_id(device_id):
            return {}
        device_filter = f'|> filter(fn: (r) => r.device_id == "{device_id}")'

    client = InfluxDBClient(
        url=settings.influxdb_url,
        token=settings.influxdb_admin_token,
        org=org_id,
    )
    try:
        flux = f'''
from(bucket: "{BUCKET_NAME}")
  |> range(start: -{int(window_sec)}s)
  |> filter(fn: (r) => r._measurement == "telemetry")
  {device_filter}
  |> filter(fn: (r) => r._field == "{sensor_key}")
  |> sort(columns: ["_time"], desc: true)
'''
        tables = client.query_api().query(flux)
        by_device: dict[str, list[float]] = {}
        for table in tables:
            for record in table.records:
                v = record.get_value()
                dev = record.values.get("device_id") or "unknown"
                if isinstance(v, (int, float)):
                    by_device.setdefault(dev, []).append(float(v))
        return by_device
    finally:
        client.close()


def check_condition(value: float, condition: str, threshold: float | None) -> bool:
    if threshold is None:
        return False
    if condition == "above":
        return value > threshold
    if condition == "below":
        return value < threshold
    if condition == "equal":
        return abs(value - threshold) < 1e-9
    return False


def _check_trigger(
    values: list[float],
    condition: str,
    threshold: float | None,
    trigger_mode: str,
    consecutive_count: int,
    duration_sec: int,
) -> tuple[bool, float | None]:
    """Evaluate a single device's values (newest first) against the rule."""
    if not values:
        return False, None

    last_value = values[0]
    crossings = [check_condition(v, condition, threshold) for v in values]

    if trigger_mode == "consecutive":
        count = 0
        for c in crossings:
            if c:
                count += 1
            else:
                break
        return count >= consecutive_count, last_value

    if trigger_mode == "duration":
        needed = max(1, duration_sec // 60)
        return all(crossings[:needed]) and len(crossings) >= needed, last_value

    if trigger_mode == "consecutive_and_duration":
        count = 0
        for c in crossings:
            if c:
                count += 1
            else:
                break
        needed = max(1, duration_sec // 60)
        return count >= consecutive_count and count >= needed, last_value

    return False, None


def evaluate_rule(rule: dict, org_id: str) -> tuple[bool, float | None]:
    if rule["condition"] == "device_offline":
        return False, None

    device_id: str | None = rule.get("device_id") or None  # None = all devices
    sensor_key = rule["sensor_key"]
    threshold = float(rule["threshold"]) if rule["threshold"] is not None else None
    trigger_mode = rule["trigger_mode"]
    consecutive_count = rule["consecutive_count"]
    duration_sec = rule["duration_sec"]

    window_sec = max(duration_sec + 60, consecutive_count * 60 + 60)
    by_device = _flux_values_by_device(org_id, device_id, sensor_key, window_sec)

    if not by_device:
        return False, None

    for dev_values in by_device.values():
        triggered, last_value = _check_trigger(
            dev_values, rule["condition"], threshold,
            trigger_mode, consecutive_count, duration_sec,
        )
        if triggered:
            return True, last_value

    return False, None
