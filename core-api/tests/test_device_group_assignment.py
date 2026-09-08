from unittest.mock import patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "operator"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _make_tenant():
    t = MagicMock()
    t.id = TENANT_ID
    t.status = "active"
    return t


def _device_row(group_id=None):
    from datetime import datetime, timezone
    row = MagicMock()
    row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    row.device_id = "device-001"
    row.device_name = "センサー1"
    row.connection_status = "online"
    row.last_seen_at = None
    row.fw_version = None
    row.cert_not_after = None
    row.created_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    row.group_id = group_id
    return row


def test_platform_assign_device_group_success():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine, \
         patch("app.routers.tenant_devices.log_audit") as mock_log:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = _make_tenant()

        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.side_effect = [
            MagicMock(group_id=None),          # assign_device_group: 現在のgroup_id取得
            MagicMock(id="g1"),                # assign_device_group: グループ存在確認
        ]
        mock_svc_engine.connect.return_value = svc_conn

        router_conn = MagicMock()
        router_conn.__enter__ = lambda s: router_conn
        router_conn.__exit__ = MagicMock(return_value=False)
        router_conn.execute.return_value.fetchone.return_value = _device_row(group_id="g1")
        mock_engine.connect.return_value = router_conn

        resp = client.patch(
            f"/tenants/{TENANT_ID}/devices/device-001",
            json={"group_id": "g1"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json()["group_id"] == "g1"
    mock_log.assert_called_once()
    _, kwargs = mock_log.call_args
    assert kwargs["detail"] == {"old_group_id": None, "new_group_id": "g1"}


def test_platform_assign_device_group_device_not_found():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_devices.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = _make_tenant()
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = None
        mock_svc_engine.connect.return_value = svc_conn

        resp = client.patch(
            f"/tenants/{TENANT_ID}/devices/missing-device",
            json={"group_id": "g1"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_portal_assign_device_group_requires_operator_or_admin():
    resp = client.patch(
        "/tenant-portal/me/devices/device-001",
        json={"group_id": "g1"},
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403


def test_portal_assign_device_group_clears_group():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.log_audit") as mock_log:
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = MagicMock(group_id="g1")
        mock_svc_engine.connect.return_value = svc_conn

        router_conn = MagicMock()
        router_conn.__enter__ = lambda s: router_conn
        router_conn.__exit__ = MagicMock(return_value=False)
        router_conn.execute.return_value.fetchone.return_value = _device_row(group_id=None)
        mock_engine.connect.return_value = router_conn

        resp = client.patch(
            "/tenant-portal/me/devices/device-001",
            json={"group_id": None},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    assert resp.json()["group_id"] is None
    _, kwargs = mock_log.call_args
    assert kwargs["detail"] == {"old_group_id": "g1", "new_group_id": None}
