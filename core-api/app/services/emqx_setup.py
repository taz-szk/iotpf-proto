"""
EMQX 起動時セットアップ: 全ルール・コネクタ・アクションを保証する。
emqx_data ボリュームが削除された場合に自動再作成する。
"""
import time
import httpx
import logging

logger = logging.getLogger(__name__)

# ─── ingestion-service (テレメトリ/ステータス) ───────────────────────
_INGEST_CONNECTOR = "ingestion_connector"
_INGEST_ACTION = "ingestion_action"
_INGEST_RULE = "telemetry_and_status_ingest"
# トピック例: /{tenant_id}/devices/{device_id}/telemetry
# EMQX の tokens('/a/b/c', '/') → ["a", "b", "c"] (先頭セパレータは除去される)
# nth(1, ...) = tenant_id, nth(3, ...) = device_id
_INGEST_SQL = (
    "SELECT nth(1, tokens(topic, '/')) as tenant_id, "
    "nth(3, tokens(topic, '/')) as device_id, payload, "
    "CASE WHEN topic =~ '/+/devices/+/telemetry' THEN 'telemetry' ELSE 'status' END as topic_type "
    'FROM "#" WHERE topic =~ \'/+/devices/+/telemetry\' OR topic =~ \'/+/devices/+/status\''
)
_INGEST_BODY = (
    '{"tenant_id":"${tenant_id}","device_id":"${device_id}",'
    '"topic_type":"${topic_type}","payload":${payload}}'
)

# ─── core-api (接続/切断イベント) ────────────────────────────────────
_EVENT_CONNECTOR = "coreapi_connector"
_EVENT_ACTION = "device_event_action"
_EVENT_RULE = "device_connection_events"
_EVENT_BODY = (
    '{"event":"${event}","clientid":"${clientid}",'
    '"username":"${username}","peerhost":"${peerhost}",'
    '"timestamp":${timestamp}}'
)


def _login(base_url: str, user: str, password: str) -> str | None:
    try:
        resp = httpx.post(
            f"{base_url}/api/v5/login",
            json={"username": user, "password": password},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["token"]
    except Exception as e:
        logger.warning("EMQX login failed: %s", e)
        return None


def _exists(base_url: str, token: str, path: str) -> bool:
    try:
        r = httpx.get(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        return r.status_code == 200
    except Exception:
        return False


def _post(base_url: str, token: str, path: str, body: dict) -> bool:
    try:
        r = httpx.post(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=10.0,
        )
        if r.status_code not in (200, 201):
            logger.warning("EMQX POST %s -> %d: %s", path, r.status_code, r.text[:200])
        return r.status_code in (200, 201)
    except Exception as e:
        logger.warning("EMQX POST %s failed: %s", path, e)
        return False


def _ensure_connector(base_url: str, token: str, name: str, url: str) -> None:
    if not _exists(base_url, token, f"/api/v5/connectors/http:{name}"):
        ok = _post(base_url, token, "/api/v5/connectors", {
            "name": name,
            "type": "http",
            "enable": True,
            "url": url,
            "connect_timeout": "15s",
            "pool_size": 4,
            "pool_type": "random",
            "enable_pipelining": 100,
            "headers": {"content-type": "application/json"},
            "ssl": {"enable": False},
        })
        logger.info("Created EMQX connector %s: %s", name, "ok" if ok else "FAILED")
    else:
        logger.debug("EMQX connector %s already exists", name)


def _delete_action(base_url: str, token: str, name: str) -> bool:
    try:
        r = httpx.delete(
            f"{base_url}/api/v5/actions/http:{name}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        ok = r.status_code in (200, 204, 404)
        logger.info("Deleted EMQX action %s: %s", name, "ok" if ok else f"FAILED ({r.status_code})")
        return ok
    except Exception as e:
        logger.warning("EMQX DELETE action %s failed: %s", name, e)
        return False


def _create_action(base_url: str, token: str, name: str, connector: str, path: str, body: str, extra_headers: dict | None = None) -> bool:
    headers = {"content-type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    ok = _post(base_url, token, "/api/v5/actions", {
        "name": name,
        "type": "http",
        "enable": True,
        "connector": connector,
        "parameters": {
            "method": "post",
            "path": path,
            "body": body,
            "headers": headers,
        },
        "resource_opts": {
            "health_check_interval": "15s",
            "inflight_window": 100,
            "max_buffer_bytes": "256MB",
            "query_mode": "async",
            "request_ttl": "45s",
            "worker_pool_size": 4,
        },
    })
    logger.info("Created EMQX action %s: %s", name, "ok" if ok else "FAILED")
    return ok


def _recreate_action(base_url: str, token: str, name: str, connector: str, path: str, body: str, extra_headers: dict | None = None) -> None:
    """既存アクションを削除して再作成する（ヘッダー変更を反映するため）。"""
    _delete_action(base_url, token, name)
    _create_action(base_url, token, name, connector, path, body, extra_headers)


def _get_rule_id(base_url: str, token: str, name: str) -> str | None:
    try:
        r = httpx.get(
            f"{base_url}/api/v5/rules",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        for rule in r.json().get("data", []):
            if rule["name"] == name:
                return rule["id"]
    except Exception:
        pass
    return None


def _delete_rule(base_url: str, token: str, name: str) -> bool:
    rule_id = _get_rule_id(base_url, token, name)
    if not rule_id:
        return False
    try:
        r = httpx.delete(
            f"{base_url}/api/v5/rules/{rule_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        ok = r.status_code in (200, 204)
        logger.info("Deleted EMQX rule %s: %s", name, "ok" if ok else f"FAILED ({r.status_code})")
        return ok
    except Exception as e:
        logger.warning("EMQX DELETE rule %s failed: %s", name, e)
        return False


def _create_rule(base_url: str, token: str, name: str, sql: str, actions: list) -> bool:
    ok = _post(base_url, token, "/api/v5/rules", {
        "name": name,
        "enable": True,
        "sql": sql,
        "actions": actions,
    })
    logger.info("Created EMQX rule %s: %s", name, "ok" if ok else "FAILED")
    return ok


def _ensure_rule(base_url: str, token: str, existing_names: set, name: str, sql: str, actions: list) -> None:
    if name not in existing_names:
        _create_rule(base_url, token, name, sql, actions)
    else:
        logger.debug("EMQX rule %s already exists", name)


def _recreate_rule(base_url: str, token: str, name: str, sql: str, actions: list) -> None:
    """既存ルールを削除して再作成する（SQL変更を反映するため）。"""
    _delete_rule(base_url, token, name)
    _create_rule(base_url, token, name, sql, actions)


def _sync_connected_clients(base_url: str, token: str) -> None:
    """EMQX に現在接続中のクライアントの online 状態を PostgreSQL/InfluxDB に同期する。"""
    try:
        resp = httpx.get(
            f"{base_url}/api/v5/clients",
            params={"conn_state": "connected", "limit": 1000},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        clients = [c for c in resp.json().get("data", []) if c.get("connected")]
        if not clients:
            logger.info("sync_connected_clients: no connected clients")
            return

        from app.routers.emqx_events import handle_device_event, DeviceEvent

        for c in clients:
            event = DeviceEvent(
                clientid=c.get("clientid", ""),
                event="client.connected",
                username=c.get("username", ""),
                peerhost=c.get("ip_address", ""),
                timestamp=None,
            )
            try:
                handle_device_event(event)
                logger.info("Synced connected client: %s", c["clientid"])
            except Exception as e:
                logger.warning("Error syncing client %s: %s", c.get("clientid"), e)
    except Exception as e:
        logger.warning("sync_connected_clients failed: %s", e)


def ensure_emqx_rules(base_url: str, user: str, password: str, webhook_secret: str, retries: int = 5) -> None:
    for attempt in range(retries):
        token = _login(base_url, user, password)
        if token:
            break
        logger.info("Waiting for EMQX (attempt %d/%d)…", attempt + 1, retries)
        time.sleep(5)
    else:
        logger.error("EMQX unreachable after %d retries; skipping rule setup", retries)
        return

    auth_header = {"x-api-key": webhook_secret}

    # コネクタ
    _ensure_connector(base_url, token, _INGEST_CONNECTOR, "http://ingestion-service:8001")
    _ensure_connector(base_url, token, _EVENT_CONNECTOR, "http://core-api:8000")

    # アクション（毎回再作成して認証ヘッダーを最新に保つ）
    _recreate_action(base_url, token, _INGEST_ACTION, _INGEST_CONNECTOR, "/ingest", _INGEST_BODY, auth_header)
    _recreate_action(base_url, token, _EVENT_ACTION, _EVENT_CONNECTOR, "/emqx/events", _EVENT_BODY, auth_header)

    # telemetry_and_status_ingest ルールは SQL 修正のため強制再作成
    _recreate_rule(
        base_url, token,
        _INGEST_RULE, _INGEST_SQL,
        [f"http:{_INGEST_ACTION}"],
    )

    # device_connection_events ルールは存在しない場合のみ作成
    try:
        rules_resp = httpx.get(
            f"{base_url}/api/v5/rules",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        existing_names = {r["name"] for r in rules_resp.json().get("data", [])}
    except Exception:
        existing_names = set()

    _ensure_rule(
        base_url, token, existing_names,
        _EVENT_RULE,
        'SELECT clientid, event, username, peerhost, timestamp '
        'FROM "$events/client_connected", "$events/client_disconnected"',
        [f"http:{_EVENT_ACTION}"],
    )

    # 現在接続中のクライアントを PostgreSQL/InfluxDB に同期
    _sync_connected_clients(base_url, token)
