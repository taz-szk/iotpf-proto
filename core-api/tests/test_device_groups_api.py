from unittest.mock import patch, MagicMock
import uuid
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
GROUP_ID = "22222222-2222-2222-2222-222222222222"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "admin"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _conn():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_platform_list_groups_empty():
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = []
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.get(f"/tenants/{TENANT_ID}/groups", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_platform_create_group_success():
    conn = _conn()
    row = MagicMock(id=uuid.uuid4(), description=None, created_at=None)
    row.name = "拠点A"
    conn.execute.return_value.fetchone.return_value = row
    with patch("app.services.device_groups.engine") as mock_engine, \
         patch("app.routers.device_groups.log_audit") as mock_log:
        mock_engine.connect.return_value = conn
        resp = client.post(
            f"/tenants/{TENANT_ID}/groups", json={"name": "拠点A"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 201
    assert resp.json()["name"] == "拠点A"
    mock_log.assert_called_once()


def test_platform_create_group_duplicate_name_returns_409():
    from sqlalchemy.exc import IntegrityError
    conn = _conn()
    conn.execute.side_effect = IntegrityError("stmt", {}, Exception("dup"))
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.post(
            f"/tenants/{TENANT_ID}/groups", json={"name": "拠点A"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 409


def test_platform_delete_group_in_use_returns_409_with_rules():
    conn = _conn()
    existing_row = MagicMock(id=GROUP_ID)
    rule_row = MagicMock(id="r1", sensor_key="temperature", condition="above", severity="warning")
    conn.execute.return_value.fetchone.side_effect = [existing_row]
    conn.execute.return_value.fetchall.return_value = [rule_row]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.delete(f"/tenants/{TENANT_ID}/groups/{GROUP_ID}", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["alert_rules"][0]["sensor_key"] == "temperature"


def test_platform_update_group_rejects_invalid_group_id_format():
    resp = client.patch(
        f"/tenants/{TENANT_ID}/groups/not-a-uuid", json={"name": "拠点B"},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_platform_delete_group_rejects_invalid_group_id_format():
    resp = client.delete(
        f"/tenants/{TENANT_ID}/groups/not-a-uuid",
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_portal_update_group_rejects_invalid_group_id_format():
    resp = client.patch(
        "/tenant-portal/me/groups/not-a-uuid", json={"name": "拠点B"},
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422


def test_portal_delete_group_rejects_invalid_group_id_format():
    resp = client.delete(
        "/tenant-portal/me/groups/not-a-uuid",
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422


def test_platform_groups_requires_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/groups")
    assert resp.status_code == 403


def test_portal_list_groups_empty():
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = []
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.get("/tenant-portal/me/groups", cookies={"iot_token": _tenant_token("viewer")})
    assert resp.status_code == 200
    assert resp.json() == []


def test_portal_create_group_requires_admin():
    resp = client.post(
        "/tenant-portal/me/groups", json={"name": "拠点A"},
        cookies={"iot_token": _tenant_token("operator")},
    )
    assert resp.status_code == 403


def test_portal_create_group_success():
    conn = _conn()
    row = MagicMock(id=uuid.uuid4(), description=None, created_at=None)
    row.name = "拠点A"
    conn.execute.return_value.fetchone.return_value = row
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.post(
            "/tenant-portal/me/groups", json={"name": "拠点A"},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 201
