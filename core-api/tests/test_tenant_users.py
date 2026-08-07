from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})

def _make_tenant(tid="22222222-2222-2222-2222-222222222222"):
    t = MagicMock()
    t.id = tid
    t.slug = "acme"
    t.status = "active"
    t.grafana_org_id = "3"
    return t

def test_create_tenant_user_success():
    tenant = _make_tenant()
    with patch("app.routers.tenant_users.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_users.engine") as mock_engine, \
         patch("app.routers.tenant_users.ensure_grafana_user_in_org") as mock_grafana:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.post(
            "/tenants/22222222-2222-2222-2222-222222222222/users",
            json={"email": "user@acme.com", "password": "secure1234", "role": "viewer"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "user@acme.com"
    assert data["role"] == "viewer"
    mock_grafana.assert_called_once_with(3, "user@acme.com", "Viewer")

def test_create_tenant_user_unauthorized():
    resp = client.post(
        "/tenants/22222222-2222-2222-2222-222222222222/users",
        json={"email": "x@x.com", "password": "x", "role": "viewer"},
    )
    assert resp.status_code == 403

def test_list_tenant_users_success():
    tenant = _make_tenant()
    with patch("app.routers.tenant_users.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_users.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_row = MagicMock()
        mock_row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        mock_row.email = "user@acme.com"
        mock_row.role = "viewer"
        mock_row.is_active = True
        mock_row.created_at = "2026-01-01T00:00:00"
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.get(
            "/tenants/22222222-2222-2222-2222-222222222222/users",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
