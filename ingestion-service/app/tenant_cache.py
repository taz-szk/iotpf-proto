import time
import psycopg2
from app.config import settings

_cache: dict[str, tuple[dict, float]] = {}
_device_name_cache: dict[str, tuple[str, float]] = {}


def get_device_name(tenant_id: str, device_id: str) -> str:
    key = f"{tenant_id}:{device_id}"
    now = time.time()
    if key in _device_name_cache:
        name, ts = _device_name_cache[key]
        if now - ts < settings.tenant_cache_ttl_sec:
            return name

    schema = f"tenant_{tenant_id.replace('-', '_')}"
    try:
        conn = psycopg2.connect(settings.postgres_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT device_name FROM "{schema}".devices WHERE device_id = %s',
                    (device_id,)
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        row = None

    name = (row[0] if row and row[0] else None) or device_id
    _device_name_cache[key] = (name, now)
    return name


def get_tenant_influx_config(tenant_id: str) -> dict | None:
    now = time.time()
    if tenant_id in _cache:
        data, ts = _cache[tenant_id]
        if now - ts < settings.tenant_cache_ttl_sec:
            return data

    try:
        conn = psycopg2.connect(settings.postgres_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT influxdb_org_id, influxdb_token FROM tenants WHERE id = %s AND status = 'active'",
                    (tenant_id,)
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        print(f"tenant_cache DB error for {tenant_id}: {e}")
        return None

    if not row or not row[0]:
        return None

    data = {"org_id": row[0], "token": row[1]}
    _cache[tenant_id] = (data, now)
    return data

def invalidate(tenant_id: str) -> None:
    _cache.pop(tenant_id, None)
