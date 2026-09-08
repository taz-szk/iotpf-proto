from unittest.mock import patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
GROUP_ID = "22222222-2222-2222-2222-222222222222"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "operator"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def test_platform_create_alert_rule_rejects_both_device_and_group():
    resp = client.post(
        f"/tenants/{TENANT_ID}/alert-rules",
        json={"device_id": "dev-1", "group_id": GROUP_ID, "sensor_key": "temperature", "condition": "above", "threshold": 30},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_platform_create_alert_rule_with_group_id_succeeds():
    with patch("app.routers.alert_rules.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        resp = client.post(
            f"/tenants/{TENANT_ID}/alert-rules",
            json={"group_id": GROUP_ID, "sensor_key": "temperature", "condition": "above", "threshold": 30},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 201
    assert resp.json()["group_id"] == GROUP_ID
    assert mock_db.execute.called


def test_platform_create_alert_rule_rejects_invalid_group_id_format():
    resp = client.post(
        f"/tenants/{TENANT_ID}/alert-rules",
        json={"group_id": "g1", "sensor_key": "temperature", "condition": "above", "threshold": 30},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_platform_update_alert_rule_rejects_conflicting_group_when_device_already_set():
    row = MagicMock(id="r1", device_id="dev-1", group_id=None)
    with patch("app.routers.alert_rules.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.execute.return_value.fetchone.return_value = row
        resp = client.patch(
            f"/tenants/{TENANT_ID}/alert-rules/r1",
            json={"group_id": GROUP_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_platform_update_alert_rule_rejects_invalid_group_id_format():
    resp = client.patch(
        f"/tenants/{TENANT_ID}/alert-rules/r1",
        json={"group_id": "not-a-uuid"},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_portal_create_alert_rule_rejects_both_device_and_group():
    resp = client.post(
        "/tenant-portal/me/alert-rules",
        json={"device_id": "dev-1", "group_id": GROUP_ID, "sensor_key": "temperature", "condition": "above", "threshold": 30},
        cookies={"iot_token": _tenant_token()},
    )
    assert resp.status_code == 422


def test_portal_create_alert_rule_with_group_id_succeeds():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        resp = client.post(
            "/tenant-portal/me/alert-rules",
            json={"group_id": GROUP_ID, "sensor_key": "temperature", "condition": "above", "threshold": 30},
            cookies={"iot_token": _tenant_token()},
        )
    assert resp.status_code == 201
    assert resp.json()["group_id"] == GROUP_ID


def test_portal_create_alert_rule_rejects_invalid_group_id_format():
    resp = client.post(
        "/tenant-portal/me/alert-rules",
        json={"group_id": "g1", "sensor_key": "temperature", "condition": "above", "threshold": 30},
        cookies={"iot_token": _tenant_token()},
    )
    assert resp.status_code == 422


def test_portal_update_alert_rule_rejects_invalid_group_id_format():
    resp = client.patch(
        "/tenant-portal/me/alert-rules/r1",
        json={"group_id": "not-a-uuid"},
        cookies={"iot_token": _tenant_token()},
    )
    assert resp.status_code == 422
