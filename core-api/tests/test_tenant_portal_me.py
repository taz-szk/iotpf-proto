from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_cookie(role="admin"):
    token = create_access_token({"sub": "user-1", "email": "admin@tenant.example", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}


def _fake_tenant():
    tenant = MagicMock()
    tenant.name = "テストテナント"
    tenant.slug = "test-tenant"
    tenant.grafana_org_id = None
    tenant.public_token = None
    return tenant


def _session_ctx(query_result):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = query_result
    return mock_db


def test_me_requires_tenant_auth():
    resp = client.get("/tenant-portal/me")
    assert resp.status_code == 401


def test_me_reports_assistant_configured_true():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True):
        mock_session.return_value = _session_ctx(_fake_tenant())
        resp = client.get("/tenant-portal/me", cookies=_tenant_cookie("admin"))
    assert resp.status_code == 200
    assert resp.json()["assistant_configured"] is True


def test_me_reports_assistant_configured_false():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=False):
        mock_session.return_value = _session_ctx(_fake_tenant())
        resp = client.get("/tenant-portal/me", cookies=_tenant_cookie("admin"))
    assert resp.status_code == 200
    assert resp.json()["assistant_configured"] is False
