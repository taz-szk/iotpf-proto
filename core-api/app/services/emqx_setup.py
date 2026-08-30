"""
EMQX 起動時セットアップ: 全ルール・Webhookブリッジを保証する。
emqx_data ボリュームが削除された場合に自動再作成する。

/api/v5/bridges (webhook type) を使用する。
Connector+Action方式はEMQX 5.8.x でconnector_not_found_or_wrong_typeエラーが
発生するため使用しない。
"""
import time
import httpx
import logging

logger = logging.getLogger(__name__)

_INGEST_BRIDGE = "ingestion_bridge"
_EVENT_BRIDGE  = "event_bridge"

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
_EVENT_BODY = (
    '{"event":"${event}","clientid":"${clientid}",'
    '"username":"${username}","peerhost":"${peerhost}",'
    '"timestamp":${timestamp}}'
)
_EVENT_SQL = (
    'SELECT clientid, event, username, peerhost, timestamp '
    'FROM "$events/client_connected", "$events/client_disconnected"'
)

_INGEST_RULE = "telemetry_and_status_ingest"
_EVENT_RULE  = "device_connection_events"


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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _bridge_exists(base_url: str, token: str, name: str) -> bool:
    try:
        r = httpx.get(
            f"{base_url}/api/v5/bridges/webhook:{name}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        return r.status_code == 200
    except Exception:
        return False


def _delete_bridge(base_url: str, token: str, name: str) -> None:
    try:
        r = httpx.delete(
            f"{base_url}/api/v5/bridges/webhook:{name}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if r.status_code in (200, 204, 404):
            logger.info("Deleted EMQX bridge %s: ok", name)
        else:
            logger.warning("Delete bridge %s: %d %s", name, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("Delete bridge %s failed: %s", name, e)


def _create_bridge(base_url: str, token: str, name: str, url: str, body: str, secret: str) -> bool:
    try:
        r = httpx.post(
            f"{base_url}/api/v5/bridges",
            headers=_auth(token),
            json={
                "name": name,
                "type": "webhook",
                "enable": True,
                "url": url,
                "method": "post",
                "headers": {
                    "content-type": "application/json",
                    "x-api-key": secret,
                },
                "body": body,
                "connect_timeout": "15s",
                "pool_size": 4,
                "pool_type": "random",
                "enable_pipelining": 100,
                "resource_opts": {
                    "health_check_interval": "15s",
                    "inflight_window": 100,
                    "max_buffer_bytes": "256MB",
                    "query_mode": "async",
                    "request_ttl": "45s",
                    "worker_pool_size": 4,
                },
            },
            timeout=15.0,
        )
        if r.status_code in (200, 201):
            logger.info("Created EMQX bridge %s: ok", name)
            return True
        logger.warning("Create bridge %s: %d %s", name, r.status_code, r.text[:300])
        return False
    except Exception as e:
        logger.warning("Create bridge %s failed: %s", name, e)
        return False


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


def _delete_rule(base_url: str, token: str, name: str) -> None:
    rule_id = _get_rule_id(base_url, token, name)
    if not rule_id:
        return
    try:
        r = httpx.delete(
            f"{base_url}/api/v5/rules/{rule_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if r.status_code in (200, 204):
            logger.info("Deleted EMQX rule %s: ok", name)
        else:
            logger.warning("Delete rule %s: %d %s", name, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("Delete rule %s failed: %s", name, e)


def _create_rule(base_url: str, token: str, name: str, sql: str, actions: list) -> bool:
    try:
        r = httpx.post(
            f"{base_url}/api/v5/rules",
            headers=_auth(token),
            json={"name": name, "enable": True, "sql": sql, "actions": actions},
            timeout=10.0,
        )
        if r.status_code in (200, 201):
            logger.info("Created EMQX rule %s: ok", name)
            return True
        logger.warning("Create rule %s: %d %s", name, r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.warning("Create rule %s failed: %s", name, e)
        return False


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


def ensure_emqx_rules(base_url: str, user: str, password: str, webhook_secret: str, retries: int = 20) -> None:
    for attempt in range(retries):
        token = _login(base_url, user, password)
        if token:
            break
        logger.info("Waiting for EMQX (attempt %d/%d)…", attempt + 1, retries)
        time.sleep(5)
    else:
        logger.error("EMQX unreachable after %d retries; skipping rule setup", retries)
        return

    # ルールを先に削除（ブリッジへの依存を解除）
    _delete_rule(base_url, token, _INGEST_RULE)
    _delete_rule(base_url, token, _EVENT_RULE)

    # ブリッジを削除して再作成（設定変更を確実に反映）
    _delete_bridge(base_url, token, _INGEST_BRIDGE)
    _delete_bridge(base_url, token, _EVENT_BRIDGE)

    ingest_ok = _create_bridge(
        base_url, token, _INGEST_BRIDGE,
        "http://ingestion-service:8001/ingest",
        _INGEST_BODY, webhook_secret,
    )
    event_ok = _create_bridge(
        base_url, token, _EVENT_BRIDGE,
        "http://core-api:8000/emqx/events",
        _EVENT_BODY, webhook_secret,
    )

    if not ingest_ok or not event_ok:
        logger.error("EMQX bridge creation failed; rules will not be created")
        return

    _create_rule(base_url, token, _INGEST_RULE, _INGEST_SQL,
                 [f"webhook:{_INGEST_BRIDGE}"])
    _create_rule(base_url, token, _EVENT_RULE, _EVENT_SQL,
                 [f"webhook:{_EVENT_BRIDGE}"])

    logger.info("EMQX rule setup complete")

    _sync_connected_clients(base_url, token)
