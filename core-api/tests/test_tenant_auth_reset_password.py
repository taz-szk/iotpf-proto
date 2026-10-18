from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _token_row(expires_delta=timedelta(hours=1), tenant_active=True, user_active=True):
    token_row = MagicMock(
        id="r1", tenant_id="11111111-1111-1111-1111-111111111111",
        user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", email="user@acme.com",
        expires_at=datetime.now(timezone.utc) + expires_delta,
    )
    tenant = MagicMock(id=token_row.tenant_id, status="active" if tenant_active else "suspended")
    user_row = MagicMock() if user_active else None
    return token_row, tenant, user_row

def test_reset_password_rejects_unknown_token():
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/tenant-auth/reset-password", json={"token": "bad", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 404

def test_reset_password_rejects_expired_token():
    token_row, tenant, user_row = _token_row(expires_delta=timedelta(hours=-1))
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.return_value = token_row
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 410
    assert db.delete.called

def test_reset_password_rejects_when_tenant_inactive():
    token_row, tenant, user_row = _token_row(tenant_active=False)
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [token_row, tenant]
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 404
    assert db.delete.called

def test_reset_password_rejects_when_user_inactive():
    token_row, tenant, _ = _token_row(user_active=False)
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [token_row, tenant]
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 404
    assert db.delete.called

def test_reset_password_updates_hash_and_deletes_token():
    token_row, tenant, _ = _token_row()
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.forget_session") as mock_forget:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [token_row, tenant]
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = MagicMock()
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 204
    assert db.delete.called
    assert mock_forget.called
