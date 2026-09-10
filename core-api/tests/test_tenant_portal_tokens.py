from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_jwt(role: str = "admin"):
    return create_access_token({
        "sub": "user-id",
        "email": "user@acme.com",
        "type": "tenant",
        "role": role,
        "tenant_id": _TENANT_ID,
    })


def test_create_token_rejects_null_max_devices():
    resp = client.post(
        "/tenant-portal/me/tokens",
        json={"max_devices": None},
        cookies={"iot_token": _tenant_jwt()},
    )
    assert resp.status_code == 422


def test_create_token_rejects_zero_max_devices():
    resp = client.post(
        "/tenant-portal/me/tokens",
        json={"max_devices": 0},
        cookies={"iot_token": _tenant_jwt()},
    )
    assert resp.status_code == 422
