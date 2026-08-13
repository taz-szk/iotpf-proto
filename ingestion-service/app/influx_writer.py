from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime, timezone
from app.config import settings

BUCKET_NAME = "telemetry"

def _ensure_bucket(client: InfluxDBClient, org_id: str) -> None:
    buckets_api = client.buckets_api()
    try:
        existing = buckets_api.find_buckets(name=BUCKET_NAME, org_id=org_id).buckets
    except Exception:
        existing = []
    if not existing:
        buckets_api.create_bucket(bucket_name=BUCKET_NAME, org_id=org_id)

def write_device_status(
    org_id: str,
    tenant_id: str,
    device_id: str,
    device_name: str,
    status: str,
) -> None:
    online_value = 1 if status == "online" else 0
    admin_client = InfluxDBClient(url=settings.influxdb_url, token=settings.influxdb_admin_token, org=org_id)
    try:
        _ensure_bucket(admin_client, org_id)
        write_api = admin_client.write_api(write_options=SYNCHRONOUS)
        point = Point("device_status") \
            .tag("tenant_id", tenant_id) \
            .tag("device_id", device_id) \
            .tag("device_name", device_name) \
            .field("online", online_value) \
            .time(datetime.now(timezone.utc))
        write_api.write(bucket=BUCKET_NAME, record=point)
    finally:
        admin_client.close()


def write_telemetry(
    org_id: str,
    tenant_id: str,
    device_id: str,
    device_name: str,
    measurements: dict,
    timestamp: datetime | None = None,
) -> None:
    ts = timestamp or datetime.now(timezone.utc)
    admin_client = InfluxDBClient(url=settings.influxdb_url, token=settings.influxdb_admin_token, org=org_id)
    try:
        _ensure_bucket(admin_client, org_id)
        write_api = admin_client.write_api(write_options=SYNCHRONOUS)
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
        admin_client.close()
