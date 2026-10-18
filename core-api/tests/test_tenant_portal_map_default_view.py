from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _cookie(role="admin"):
    token = create_access_token({"sub": "u1", "email": "a@acme.com", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}

def _session_ctx(tenant):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant
    return mock_db

def test_me_includes_map_settings():
    tenant = MagicMock(map_default_lat=35.0, map_default_lng=139.0, map_default_zoom=8, grafana_org_id=None, public_token=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=False):
        mock_sl.return_value = _session_ctx(tenant)
        resp = client.get("/tenant-portal/me", cookies=_cookie())
    assert resp.status_code == 200
    body = resp.json()
    assert body["map_default_lat"] == 35.0
    assert "map_tile_url_template" in body

def test_update_map_default_view_rejects_invalid_latitude():
    with patch("app.routers.tenant_portal.SessionLocal"):
        resp = client.put("/tenant-portal/me/map-default-view", json={"lat": 999, "lng": 139.0, "zoom": 8}, cookies=_cookie())
    assert resp.status_code == 422

def test_update_map_default_view_saves_values():
    tenant = MagicMock(id=TENANT_ID)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        db = _session_ctx(tenant)
        mock_sl.return_value = db
        resp = client.put("/tenant-portal/me/map-default-view", json={"lat": 35.5, "lng": 139.5, "zoom": 10}, cookies=_cookie())
    assert resp.status_code == 200
    assert tenant.map_default_lat == 35.5
    assert db.commit.called

def test_update_map_default_view_requires_admin_or_operator():
    resp = client.put("/tenant-portal/me/map-default-view", json={"lat": 35.5, "lng": 139.5, "zoom": 10}, cookies=_cookie(role="viewer"))
    assert resp.status_code == 403
