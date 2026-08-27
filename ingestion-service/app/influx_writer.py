from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime, timezone
from app.config import settings

BUCKET_NAME = "telemetry"

def _ensure_bucket(client: InfluxDBClient, org_id: str) -> None:
    buckets_api = client.buckets_api()
    try:
        existing = buckets_api.find_buckets(name=BUCKET_NAME, org_id=org_id).buckets or []
    except Exception:
        existing = []
    if not existing:
        try:
            buckets_api.create_bucket(bucket_name=BUCKET_NAME, org_id=org_id)
        except Exception:
            pass  # 既に存在する場合（race condition等）は無視

def write_device_status(
    org_id: str,
    token: str,
    tenant_id: str,
    device_id: str,
    device_name: str,
    status: str,
) -> None:
    online_value = 1 if status == "online" else 0
    client = InfluxDBClient(url=settings.influxdb_url, token=token, org=org_id)
    try:
        _ensure_bucket(client, org_id)
        write_api = client.write_api(write_options=SYNCHRONOUS)
        point = Point("device_status") \
            .tag("tenant_id", tenant_id) \
            .tag("device_id", device_id) \
            .tag("device_name", device_name) \
            .field("online", online_value) \
            .time(datetime.now(timezone.utc))
        write_api.write(bucket=BUCKET_NAME, record=point)
    finally:
        client.close()


def write_telemetry(
    org_id: str,
    token: str,
    tenant_id: str,
    device_id: str,
    device_name: str,
    measurements: dict,
    timestamp: datetime | None = None,
) -> None:
    ts = timestamp or datetime.now(timezone.utc)
    client = InfluxDBClient(url=settings.influxdb_url, token=token, org=org_id)
    try:
        _ensure_bucket(client, org_id)
        write_api = client.write_api(write_options=SYNCHRONOUS)
        point = Point("telemetry") \
            .tag("tenant_id", tenant_id) \
            .tag("device_id", device_id) \
            .tag("device_name", device_name) \
            .time(ts)
        for key, value in measurements.items():
            if isinstance(value, (int, float)):
                point = point.field(key, float(value))
        write_api.write(bucket=BUCKET_NAME, record=point)
    finally:
        client.close()
