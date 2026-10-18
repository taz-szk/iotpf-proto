from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.rate_limiter import clear_failures

client = TestClient(app)

def _body(slug="acme", email="user@acme.com"):
    return {"tenant_slug": slug, "email": email, "recaptcha_token": "good-token"}

def test_forgot_password_returns_same_response_when_tenant_not_found():
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}

def test_forgot_password_returns_same_response_when_user_not_found():
    clear_failures("tenant_password_reset:testclient")
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}

def test_forgot_password_creates_token_and_queues_email_when_found():
    clear_failures("tenant_password_reset:testclient")
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    row = MagicMock(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", email="user@acme.com")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.BackgroundTasks.add_task") as mock_add_task:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [tenant, None]
        # 1回目: Tenant検索、2回目: 既存PasswordResetTokenの検索(無し)
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = row
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}
    assert db.add.called
    assert mock_add_task.called

def test_forgot_password_low_recaptcha_score_returns_same_response():
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=False):
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}
