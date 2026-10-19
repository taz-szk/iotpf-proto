from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _admin_token():
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": "admin"})


def _token(role: str):
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": role})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_put_export_schedule_rejects_invalid_frequency():
    resp = client.put("/tenant-portal/me/export-schedule",
                       json={"frequency": "hourly", "device_ids": None, "group_id": None, "enabled": True},
                       cookies={"iot_token": _admin_token()})
    assert resp.status_code == 422


def test_put_export_schedule_rejects_non_admin_role():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put("/tenant-portal/me/export-schedule",
                           json={"frequency": "daily", "device_ids": None, "group_id": None, "enabled": True},
                           cookies={"iot_token": _token("viewer")})
    assert resp.status_code == 403


def test_put_export_schedule_creates_schedule():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = db
        resp = client.put("/tenant-portal/me/export-schedule",
                           json={"frequency": "weekly", "device_ids": None, "group_id": None, "enabled": True},
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 200
    assert resp.json()["frequency"] == "weekly"
    db.add.assert_called_once()


def test_put_export_schedule_rejects_device_not_belonging_to_tenant():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.services.export_jobs._fetch_existing_device_ids", return_value={"dev-1"}):
        db = _session_ctx()
        mock_session.return_value = db
        resp = client.put("/tenant-portal/me/export-schedule",
                           json={"frequency": "daily", "device_ids": ["dev-1", "other-tenant-dev"],
                                 "group_id": None, "enabled": True},
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 422
    assert "other-tenant-dev" in resp.json()["detail"]
    db.add.assert_not_called()


def test_put_export_schedule_rejects_unknown_group():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.group_exists", return_value=False):
        db = _session_ctx()
        mock_session.return_value = db
        resp = client.put("/tenant-portal/me/export-schedule",
                           json={"frequency": "daily", "device_ids": None,
                                 "group_id": "33333333-3333-3333-3333-333333333333", "enabled": True},
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 422
    db.add.assert_not_called()


def test_put_export_schedule_rejects_non_uuid_group_id():
    resp = client.put("/tenant-portal/me/export-schedule",
                       json={"frequency": "daily", "device_ids": None, "group_id": "not-a-uuid", "enabled": True},
                       cookies={"iot_token": _admin_token()})
    assert resp.status_code == 422


def test_get_export_schedule_returns_default_when_none_exists():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/export-schedule",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 200
    assert resp.json() == {"frequency": None, "device_ids": None, "group_id": None, "enabled": False}
