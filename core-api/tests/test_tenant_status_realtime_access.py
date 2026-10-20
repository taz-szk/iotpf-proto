from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import uuid
from app.main import app
from app.models.public import Tenant
from app.services.auth import create_access_token
from app.services.realtime_access import InfluxAuthorizationError, set_readonly_token_active

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_headers():
    t = create_access_token({"sub": "33333333-3333-3333-3333-333333333333", "email": "p@test.com", "type": "platform"})
    return {"Authorization": f"Bearer {t}"}


def _tenant(status):
    return Tenant(id=uuid.UUID(TENANT_ID), name="T", slug="t", status=status, grafana_org_id="1",
                  signup_source="platform_created",
                  created_at=__import__("datetime").datetime(2026, 1, 1))


def _db(tenant, rt_token):
    db = MagicMock(); db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)

    def query(model):
        q = MagicMock()
        q.filter.return_value.first.return_value = tenant if model.__name__ == "Tenant" else rt_token
        return q
    db.query.side_effect = query
    return db


def test_set_readonly_token_active_patches_status():
    resp = MagicMock(status_code=200)
    with patch("app.services.realtime_access.httpx.patch", return_value=resp) as p:
        set_readonly_token_active("auth-1", False)
    assert p.call_args.kwargs["json"] == {"status": "inactive"}
    assert "auth-1" in p.call_args.args[0]


def test_suspend_deactivates_token_then_updates_status():
    tenant = _tenant("active")
    with patch("app.routers.tenants.SessionLocal") as ms, \
         patch("app.routers.tenants.set_readonly_token_active") as mock_set:
        ms.return_value = _db(tenant, MagicMock(influx_authorization_id="a1"))
        resp = client.patch(f"/tenants/{TENANT_ID}/status", json={"status": "suspended"}, headers=_platform_headers())
    assert resp.status_code == 200
    mock_set.assert_called_once_with("a1", False)
    assert tenant.status == "suspended"


def test_activate_reactivates_token():
    tenant = _tenant("suspended")
    with patch("app.routers.tenants.SessionLocal") as ms, \
         patch("app.routers.tenants.set_readonly_token_active") as mock_set:
        ms.return_value = _db(tenant, MagicMock(influx_authorization_id="a1"))
        client.patch(f"/tenants/{TENANT_ID}/status", json={"status": "active"}, headers=_platform_headers())
    mock_set.assert_called_once_with("a1", True)


def test_suspend_fails_without_changing_status_if_influx_fails():
    tenant = _tenant("active")
    with patch("app.routers.tenants.SessionLocal") as ms, \
         patch("app.routers.tenants.set_readonly_token_active", side_effect=InfluxAuthorizationError("x")):
        db = _db(tenant, MagicMock(influx_authorization_id="a1"))
        ms.return_value = db
        resp = client.patch(f"/tenants/{TENANT_ID}/status", json={"status": "suspended"}, headers=_platform_headers())
    assert resp.status_code == 502
    assert tenant.status == "active"
    db.commit.assert_not_called()


def test_suspend_without_token_skips_influx():
    tenant = _tenant("active")
    with patch("app.routers.tenants.SessionLocal") as ms, \
         patch("app.routers.tenants.set_readonly_token_active") as mock_set:
        ms.return_value = _db(tenant, None)
        resp = client.patch(f"/tenants/{TENANT_ID}/status", json={"status": "suspended"}, headers=_platform_headers())
    assert resp.status_code == 200
    mock_set.assert_not_called()
