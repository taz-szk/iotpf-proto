import psycopg2
import psycopg2.extras
from app.config import settings

def get_conn():
    return psycopg2.connect(settings.postgres_dsn, cursor_factory=psycopg2.extras.RealDictCursor)

def get_all_tenants() -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, influxdb_org_id, influxdb_token FROM tenants WHERE status = 'active'"
            )
            return cur.fetchall()
    finally:
        conn.close()

def get_active_alert_rules(tenant_id: str) -> list[dict]:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT id::text, device_id, sensor_key, condition, threshold,
                       trigger_mode, consecutive_count, duration_sec,
                       severity, notify_emails
                FROM "{schema}".alert_rules WHERE is_active = TRUE
            ''')
            return cur.fetchall()
    finally:
        conn.close()

def get_unresolved_event(tenant_id: str, rule_id: str, device_id: str | None) -> dict | None:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT id::text, triggered_at, notified_at
                FROM "{schema}".alert_events
                WHERE rule_id = %s AND device_id IS NOT DISTINCT FROM %s
                  AND resolved_at IS NULL
                ORDER BY triggered_at DESC LIMIT 1
            ''', (rule_id, device_id))
            return cur.fetchone()
    finally:
        conn.close()

def create_alert_event(tenant_id: str, rule_id: str, device_id: str | None, trigger_value: float | None) -> str:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    import uuid
    event_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                INSERT INTO "{schema}".alert_events (id, rule_id, device_id, trigger_value)
                VALUES (%s, %s, %s, %s)
            ''', (event_id, rule_id, device_id, trigger_value))
        conn.commit()
    finally:
        conn.close()
    return event_id

def resolve_alert_event(tenant_id: str, event_id: str) -> None:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    from datetime import datetime, timezone
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                UPDATE "{schema}".alert_events SET resolved_at = %s WHERE id = %s
            ''', (datetime.now(timezone.utc), event_id))
        conn.commit()
    finally:
        conn.close()

def mark_event_notified(tenant_id: str, event_id: str) -> None:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    from datetime import datetime, timezone
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                UPDATE "{schema}".alert_events SET notified_at = %s WHERE id = %s
            ''', (datetime.now(timezone.utc), event_id))
        conn.commit()
    finally:
        conn.close()

def get_offline_devices(tenant_id: str, threshold_sec: int) -> list[dict]:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT device_id
                FROM "{schema}".devices
                WHERE connection_status != 'offline'
                  AND last_seen_at IS NOT NULL
                  AND last_seen_at < NOW() - INTERVAL '{threshold_sec} seconds'
            ''')
            return cur.fetchall()
    finally:
        conn.close()

def mark_device_offline(tenant_id: str, device_id: str) -> None:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                UPDATE "{schema}".devices SET connection_status = 'offline' WHERE device_id = %s
            ''', (device_id,))
        conn.commit()
    finally:
        conn.close()
