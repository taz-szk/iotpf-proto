import json
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


def publish_ota_command(tenant_id: str, device_id: str, payload: dict) -> None:
    _publish(
        topic=f"/{tenant_id}/devices/{device_id}/commands",
        payload={"type": "ota", **payload},
    )
