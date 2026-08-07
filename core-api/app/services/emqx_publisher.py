import json
import httpx
from app.config import settings


def publish_ota_command(tenant_id: str, device_id: str, payload: dict) -> None:
    resp = httpx.post(
        f"{settings.emqx_api_url}/api/v5/publish",
        auth=(settings.emqx_api_user, settings.emqx_api_password),
        json={
            "topic": f"/{tenant_id}/devices/{device_id}/commands",
            "qos": 1,
            "payload": json.dumps(payload),
            "retain": False,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
