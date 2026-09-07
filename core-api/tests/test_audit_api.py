from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import uuid


def _make_token(client, user_id="user-1", email="admin@example.com"):
    from app.services.auth import create_access_token
    return create_access_token({"sub": user_id, "email": email, "type": "platform"})


def test_list_audit_logs_requires_auth(client):
    resp = client.get("/audit-logs")
    assert resp.status_code == 403  # no Bearer token


def test_list_audit_logs_empty(client):
    token = _make_token(client)
    with patch("app.routers.audit_logs.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = client.get("/audit-logs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_audit_logs_limit_max(client):
    token = _make_token(client)
    resp = client.get("/audit-logs?limit=200", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422  # limit > 100 は validation error


# ---------------------------------------------------------------------------
# テナントポータル向け監査ログAPI (Task 3)
# ---------------------------------------------------------------------------

def _make_tenant_token(client, user_id="user-1", email="admin@tenant.com", tenant_id=None, role="admin"):
    from app.services.auth import create_access_token
    tid = tenant_id or str(uuid.uuid4())
    return create_access_token({"sub": user_id, "email": email, "type": "tenant",
                                 "tenant_id": tid, "role": role}), tid


def test_tenant_audit_logs_requires_admin(client):
    from app.services.auth import create_access_token
    token = create_access_token({"sub": "u1", "email": "e@t.com", "type": "tenant",
                                  "tenant_id": str(uuid.uuid4()), "role": "viewer"})
    resp = client.get("/tenant-portal/audit-logs",
                      cookies={"iot_token": token})
    assert resp.status_code == 403


def test_tenant_audit_logs_empty(client):
    token, tid = _make_tenant_token(client)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = client.get("/tenant-portal/audit-logs", cookies={"iot_token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
