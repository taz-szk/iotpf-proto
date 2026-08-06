from influxdb_client import InfluxDBClient
from app.config import settings

BUCKET_NAME = "telemetry"

def _flux_recent_values(org_id: str, device_id: str, sensor_key: str, window_sec: int) -> list[float]:
    client = InfluxDBClient(url=settings.influxdb_url, token=settings.influxdb_admin_token, org=org_id)
    try:
        query_api = client.query_api()
        flux = f'''
from(bucket: "{BUCKET_NAME}")
  |> range(start: -{window_sec}s)
  |> filter(fn: (r) => r._measurement == "telemetry")
  |> filter(fn: (r) => r.device_id == "{device_id}")
  |> filter(fn: (r) => r._field == "{sensor_key}")
  |> sort(columns: ["_time"], desc: true)
'''
        tables = query_api.query(flux)
        values = []
        for table in tables:
            for record in table.records:
                v = record.get_value()
                if isinstance(v, (int, float)):
                    values.append(float(v))
        return values
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

def evaluate_rule(rule: dict, org_id: str) -> tuple[bool, float | None]:
    if rule["condition"] == "device_offline":
        return False, None

    device_id = rule["device_id"] or ""
    sensor_key = rule["sensor_key"]
    threshold = float(rule["threshold"]) if rule["threshold"] is not None else None
    trigger_mode = rule["trigger_mode"]
    consecutive_count = rule["consecutive_count"]
    duration_sec = rule["duration_sec"]

    window_sec = max(duration_sec + 60, consecutive_count * 60 + 60)
    values = _flux_recent_values(org_id, device_id, sensor_key, window_sec)

    if not values:
        return False, None

    last_value = values[0]
    crossings = [check_condition(v, rule["condition"], threshold) for v in values]

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
