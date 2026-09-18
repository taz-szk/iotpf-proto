"""テナントポータルの削除済み(Del_接頭辞)デバイス一覧・完全削除(purge)エンドポイントのテスト。"""
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_jwt(role="admin"):
    return create_access_token({
        "sub": "user-id", "email": "user@acme.com",
        "type": "tenant", "role": role, "tenant_id": _TENANT_ID,
    })


def _mock_tenant(influxdb_org_id="org-1", influxdb_token="tok-1"):
    tenant = MagicMock()
    tenant.influxdb_org_id = influxdb_org_id
    tenant.influxdb_token = influxdb_token
    return tenant


def test_list_archived_devices_returns_results():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.list_archived_devices") as mock_list:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _mock_tenant()
        mock_list.return_value = [
            {"device_name": "Del_dev-001", "original_name": "dev-001", "deleted_at": "2026-09-18T03:51:19Z"},
        ]
        resp = client.get("/tenant-portal/me/devices/archived", cookies={"iot_token": _tenant_jwt("viewer")})
    assert resp.status_code == 200
    assert resp.json() == [
        {"device_name": "Del_dev-001", "original_name": "dev-001", "deleted_at": "2026-09-18T03:51:19Z"},
    ]
    mock_list.assert_called_once_with("org-1", "tok-1")


def test_list_archived_devices_requires_auth():
    resp = client.get("/tenant-portal/me/devices/archived")
    assert resp.status_code == 401


def test_list_archived_devices_returns_empty_when_influxdb_not_configured():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _mock_tenant(influxdb_org_id=None)
        resp = client.get("/tenant-portal/me/devices/archived", cookies={"iot_token": _tenant_jwt("viewer")})
    assert resp.status_code == 200
    assert resp.json() == []


def test_purge_archived_devices_rejects_wrong_confirm_phrase():
    resp = client.post(
        "/tenant-portal/me/devices/archived/purge",
        json={"device_names": ["Del_dev-001"], "confirm": "delete"},
        cookies={"iot_token": _tenant_jwt("admin")},
    )
    assert resp.status_code == 422


def test_purge_archived_devices_rejects_non_del_prefixed_name():
    resp = client.post(
        "/tenant-portal/me/devices/archived/purge",
        json={"device_names": ["dev-001"], "confirm": "DELETE"},
        cookies={"iot_token": _tenant_jwt("admin")},
    )
    assert resp.status_code == 422


def test_purge_archived_devices_requires_admin_role():
    """operatorはpurgeできない(admin権限のみ許可)。"""
    resp = client.post(
        "/tenant-portal/me/devices/archived/purge",
        json={"device_names": ["Del_dev-001"], "confirm": "DELETE"},
        cookies={"iot_token": _tenant_jwt("operator")},
    )
    assert resp.status_code == 403


def test_purge_archived_devices_success_calls_purge_and_logs_audit():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.purge_archived_device_from_influxdb") as mock_purge, \
         patch("app.routers.tenant_portal.log_audit") as mock_audit:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _mock_tenant()

        resp = client.post(
            "/tenant-portal/me/devices/archived/purge",
            json={"device_names": ["Del_dev-001", "Del_dev-002"], "confirm": "DELETE"},
            cookies={"iot_token": _tenant_jwt("admin")},
        )

    assert resp.status_code == 200
    assert resp.json() == [
        {"device_name": "Del_dev-001", "status": "ok"},
        {"device_name": "Del_dev-002", "status": "ok"},
    ]
    assert mock_purge.call_count == 2
    mock_purge.assert_any_call("org-1", "Del_dev-001")
    mock_purge.assert_any_call("org-1", "Del_dev-002")
    assert mock_audit.call_count == 2


def test_purge_archived_devices_reports_per_device_failure():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.purge_archived_device_from_influxdb") as mock_purge, \
         patch("app.routers.tenant_portal.log_audit"):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _mock_tenant()
        mock_purge.side_effect = Exception("influxdb error")

        resp = client.post(
            "/tenant-portal/me/devices/archived/purge",
            json={"device_names": ["Del_dev-001"], "confirm": "DELETE"},
            cookies={"iot_token": _tenant_jwt("admin")},
        )

    assert resp.status_code == 200
    assert resp.json() == [
        {"device_name": "Del_dev-001", "status": "error", "detail": "influxdb error"},
    ]


def test_purge_archived_devices_requires_influxdb_configured():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _mock_tenant(influxdb_org_id=None)

        resp = client.post(
            "/tenant-portal/me/devices/archived/purge",
            json={"device_names": ["Del_dev-001"], "confirm": "DELETE"},
            cookies={"iot_token": _tenant_jwt("admin")},
        )
    assert resp.status_code == 404
