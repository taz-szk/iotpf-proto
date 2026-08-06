import psycopg2
from datetime import datetime, timezone
from app.config import settings
import re

def update_last_seen(tenant_id: str, device_id: str, status: str = "online") -> None:
    if not re.fullmatch(r'[0-9a-f-]{36}', tenant_id.lower()):
        return
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    now = datetime.now(timezone.utc)
    conn = psycopg2.connect(settings.postgres_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'UPDATE "{schema}".devices SET connection_status = %s, last_seen_at = %s WHERE device_id = %s',
                (status, now, device_id)
            )
        conn.commit()
    finally:
        conn.close()
