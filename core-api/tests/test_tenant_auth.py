from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)


def _make_tenant(slug="acme", grafana_org_id="2"):
    t = MagicMock()
    t.id = "11111111-1111-1111-1111-111111111111"
    t.slug = slug
    t.status = "active"
    t.grafana_org_id = grafana_org_id
    return t


def test_tenant_login_success():
    tenant = _make_tenant()
    row = MagicMock()
    row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    row.email = "user@acme.com"
    row.password_hash = "$2b$12$..."
    row.role = "viewer"

    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.verify_password", return_value=True):
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = row
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.post("/tenant-auth/login", json={"tenant_slug": "acme", "email": "user@acme.com", "password": "pass"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "user@acme.com"
    assert data["redirect_url"] == "/grafana/?orgId=2"
    assert "iot_token" in resp.cookies


def test_tenant_login_invalid_credentials():
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/tenant-auth/login", json={"tenant_slug": "x", "email": "x@x.com", "password": "x"})
    assert resp.status_code == 401


def test_verify_jwt_no_cookie():
    resp = client.get("/auth/verify-jwt")
    assert resp.status_code == 401


def test_verify_jwt_valid_tenant_cookie():
    from app.services.auth import create_access_token
    token = create_access_token({"sub": "u1", "email": "user@acme.com", "type": "tenant", "tenant_id": "t1"})
    resp = client.get("/auth/verify-jwt", cookies={"iot_token": token})
    assert resp.status_code == 200
    assert resp.headers.get("x-auth-user") == "user@acme.com"


def test_verify_jwt_valid_platform_cookie():
    from app.services.auth import create_access_token
    token = create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})
    resp = client.get("/auth/verify-jwt", cookies={"iot_token": token})
    assert resp.status_code == 200
    assert resp.headers.get("x-auth-user") == "admin@iot.local"


def test_verify_jwt_refresh_token_rejected():
    from app.services.auth import create_refresh_token
    token = create_refresh_token({"sub": "u1", "email": "user@acme.com", "type": "tenant"})
    resp = client.get("/auth/verify-jwt", cookies={"iot_token": token})
    assert resp.status_code == 401


def test_tenant_login_wrong_password():
    tenant = _make_tenant()
    row = MagicMock()
    row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    row.email = "user@acme.com"
    row.password_hash = "$2b$12$..."
    row.role = "viewer"

    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.verify_password", return_value=False):
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = row
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.post("/tenant-auth/login", json={"tenant_slug": "acme", "email": "user@acme.com", "password": "wrong"})
    assert resp.status_code == 401
