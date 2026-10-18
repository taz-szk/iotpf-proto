from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.rate_limiter import clear_failures
from app.routers.tenant_auth import _process_forgot_password

client = TestClient(app)

def _body(slug="acme", email="user@acme.com"):
    return {"tenant_slug": slug, "email": email, "recaptcha_token": "good-token"}

def test_forgot_password_returns_same_response_regardless_of_lookup_result():
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.BackgroundTasks.add_task"):
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}

def test_forgot_password_queues_background_processing_without_touching_db_synchronously():
    # DBに触れずに即座にレスポンスを返す(見つかった/見つからないの応答速度差を無くすため、
    # テナント・ユーザー検索・トークン作成は全てBackgroundTaskの中で行う)
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.BackgroundTasks.add_task") as mock_add_task:
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert not mock_sl.called
    assert not mock_engine.called
    mock_add_task.assert_called_once_with(_process_forgot_password, "acme", "user@acme.com")

def test_forgot_password_low_recaptcha_score_does_not_queue_background_task():
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=False), \
         patch("app.routers.tenant_auth.BackgroundTasks.add_task") as mock_add_task:
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}
    mock_add_task.assert_not_called()


def _session_ctx(query_results=None):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = query_results
    return mock_db

def test_process_forgot_password_does_nothing_when_tenant_not_found():
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        db = _session_ctx(query_results=None)
        mock_sl.return_value = db
        _process_forgot_password("acme", "user@acme.com")
    assert not db.add.called

def test_process_forgot_password_does_nothing_when_user_not_found():
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine:
        db = _session_ctx(query_results=tenant)
        mock_sl.return_value = db
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        _process_forgot_password("acme", "user@acme.com")
    assert not db.add.called

def test_process_forgot_password_creates_token_and_sends_email_when_found():
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    row = MagicMock(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", email="user@acme.com")
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth._send_password_reset_email_safe") as mock_send:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [tenant, None]
        # 1回目: Tenant検索、2回目: 既存PasswordResetTokenの検索(無し)
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = row
        _process_forgot_password("acme", "user@acme.com")
    assert db.add.called
    assert db.commit.called
    assert mock_send.called

def test_process_forgot_password_deletes_existing_token_before_creating_new_one():
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    row = MagicMock(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", email="user@acme.com")
    existing_token = MagicMock()
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth._send_password_reset_email_safe"):
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [tenant, existing_token]
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = row
        _process_forgot_password("acme", "user@acme.com")
    db.delete.assert_called_once_with(existing_token)
    assert db.add.called
