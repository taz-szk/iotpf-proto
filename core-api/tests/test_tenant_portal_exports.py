from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.export_jobs import JobAlreadyRunningError, ExportTooLargeError

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _admin_token():
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": "admin"})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_request_export_rejects_when_already_running():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export", side_effect=JobAlreadyRunningError()):
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            cookies={"iot_token": _admin_token()})
    assert resp.status_code == 409


def test_request_export_rejects_when_too_large():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export",
               side_effect=ExportTooLargeError(99_999_999, 20_000_000)):
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            cookies={"iot_token": _admin_token()})
    assert resp.status_code == 422


def test_download_export_returns_404_when_not_done():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 404
