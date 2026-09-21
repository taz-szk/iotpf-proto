import json
from urllib.parse import quote

import httpx
from app.config import settings

_token: str | None = None


def _get_token() -> str:
    global _token
    if _token:
        return _token
    resp = httpx.post(
        f"{settings.emqx_api_url}/api/v5/login",
        json={"username": settings.emqx_api_user, "password": settings.emqx_api_password},
        timeout=10.0,
    )
    resp.raise_for_status()
    _token = resp.json()["token"]
    return _token


def _publish(topic: str, payload: dict) -> None:
    global _token
    body = {"topic": topic, "qos": 1, "payload": json.dumps(payload), "retain": False}
    resp = httpx.post(
        f"{settings.emqx_api_url}/api/v5/publish",
        headers={"Authorization": f"Bearer {_get_token()}"},
        json=body,
        timeout=10.0,
    )
    if resp.status_code == 401:
        _token = None
        resp = httpx.post(
            f"{settings.emqx_api_url}/api/v5/publish",
            headers={"Authorization": f"Bearer {_get_token()}"},
            json=body,
            timeout=10.0,
        )
    resp.raise_for_status()


def kick_client(clientid: str) -> None:
    """接続中のMQTTクライアントを切断する。削除したデバイスが、証明書が有効なまま
    既存セッションで送信を続けないようにするためのベストエフォート処理。"""
    global _token
    try:
        url = f"{settings.emqx_api_url}/api/v5/clients/{quote(clientid, safe='')}"
        resp = httpx.delete(url, headers={"Authorization": f"Bearer {_get_token()}"}, timeout=10.0)
        if resp.status_code == 401:
            _token = None
            httpx.delete(url, headers={"Authorization": f"Bearer {_get_token()}"}, timeout=10.0)
    except Exception:
        pass


def publish_ota_command(tenant_id: str, device_id: str, payload: dict) -> None:
    _publish(
        topic=f"/{tenant_id}/devices/{device_id}/commands",
        payload={"type": "ota", **payload},
    )
