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
                SELECT id::text, device_id, group_id::text, sensor_key, condition, threshold,
                       trigger_mode, consecutive_count, duration_sec,
                       severity, notify_emails, slack_webhook_url, notify_status
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

def get_group_device_ids(tenant_id: str, group_id: str) -> list[str]:
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT device_id FROM "{schema}".devices WHERE group_id = %s
            ''', (group_id,))
            return [row["device_id"] for row in cur.fetchall()]
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
                  AND last_seen_at < NOW() - make_interval(secs => %s)
            ''', (threshold_sec,))
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


def record_notify_status(tenant_id: str, rule_id: str, results: dict) -> None:
    """通知の配信結果を、ルールの直近の結果として記録する。送信を試みたチャンネルだけを上書きする
    (メールだけ設定しているルールで、Slackの欄を書き換えない)。結果にはURL・宛先を含めない。"""
    import json
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    merged = {
        channel: {"ok": r["ok"], "error": r.get("error"), "at": now}
        for channel, r in (results or {}).items() if r
    }
    if not merged:
        return
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'''UPDATE "{schema}".alert_rules
                    SET notify_status = COALESCE(notify_status, '{{}}'::jsonb) || %s::jsonb
                    WHERE id = %s''',
                (json.dumps(merged), rule_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_tenant_admin_contacts(tenant_id: str) -> dict:
    """通知の失敗を知らせる先。テナント名と、有効なテナント管理者・プラットフォーム管理者のメールアドレス
    (テナント管理者が先。小文字にそろえて重複を除く)。"""
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            cur.execute(f"SELECT email FROM \"{schema}\".users WHERE role = 'admin' AND is_active = TRUE ORDER BY created_at")
            tenant_admins = [r["email"] for r in cur.fetchall()]
            cur.execute("SELECT email FROM platform_users WHERE is_active = TRUE ORDER BY created_at")
            platform_admins = [r["email"] for r in cur.fetchall()]
    finally:
        conn.close()
    seen, emails = set(), []
    for e in tenant_admins + platform_admins:
        low = (e or "").strip().lower()
        if low and low not in seen:
            seen.add(low)
            emails.append(low)
    return {"tenant_name": row["name"] if row else "", "emails": emails}


def record_admin_notice(tenant_id: str, rule_id: str, at) -> None:
    """管理者へ失敗を知らせた時刻を、ルールのnotify_statusに記録する(短時間に繰り返し送らないため)。"""
    import json

    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'''UPDATE "{schema}".alert_rules
                    SET notify_status = COALESCE(notify_status, '{{}}'::jsonb) || %s::jsonb
                    WHERE id = %s''',
                (json.dumps({"admin_notice": {"at": at.isoformat()}}), rule_id),
            )
        conn.commit()
    finally:
        conn.close()
