from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from app.main import app
from app.services.rate_limiter import clear_failures

client = TestClient(app)


def _session_ctx(query_results=None):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = query_results
    return mock_db


def _signup_body(email="user@example.com"):
    return {
        "tenant_name": "Acme Corp",
        "email": email,
        "password": "correct-horse-battery-staple",
        "recaptcha_token": "good-token",
    }


def test_signup_rejects_low_recaptcha_score():
    clear_failures("signup:testclient")
    with patch("app.routers.signup.verify_recaptcha", return_value=False):
        resp = client.post("/signup", json=_signup_body())
    assert resp.status_code == 400


def test_signup_creates_pending_request_and_sends_email():
    clear_failures("signup:testclient")
    with patch("app.routers.signup.verify_recaptcha", return_value=True), \
         patch("app.routers.signup.SessionLocal") as mock_session, \
         patch("app.routers.signup.send_signup_verification_email") as mock_mail:
        db = _session_ctx(query_results=None)  # 既存の未確認リクエスト・Tenant.slugどちらも無し
        mock_session.return_value = db
        resp = client.post("/signup", json=_signup_body())
    assert resp.status_code == 201
    assert db.add.called
    assert mock_mail.called


def test_signup_fails_when_email_send_fails_and_rolls_back():
    clear_failures("signup:testclient")
    with patch("app.routers.signup.verify_recaptcha", return_value=True), \
         patch("app.routers.signup.SessionLocal") as mock_session, \
         patch("app.routers.signup.send_signup_verification_email", side_effect=ConnectionError("smtp down")):
        db = _session_ctx(query_results=None)
        mock_session.return_value = db
        resp = client.post("/signup", json=_signup_body())
    assert resp.status_code == 500
    assert db.rollback.called


def test_verify_rejects_unknown_token():
    with patch("app.routers.signup.SessionLocal") as mock_session:
        db = _session_ctx(query_results=None)
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "does-not-exist"})
    assert resp.status_code == 404


def test_verify_rejects_expired_token():
    expired_request = MagicMock(
        id="r1", email="user@example.com", tenant_name="Acme Corp", slug="acme-corp",
        password_hash="hashed",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with patch("app.routers.signup.SessionLocal") as mock_session:
        db = _session_ctx(query_results=expired_request)
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "expired-token"})
    assert resp.status_code == 410
    assert db.delete.called


def test_verify_returns_409_on_slug_conflict():
    valid_request = MagicMock(
        id="r1", email="user@example.com", tenant_name="Acme Corp", slug="acme-corp",
        password_hash="hashed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("app.routers.signup.SessionLocal") as mock_session:
        db = _session_ctx(query_results=valid_request)
        db.flush.side_effect = IntegrityError("dup", {}, Exception("dup"))
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "valid-token"})
    assert resp.status_code == 409
    assert db.rollback.called
    db.delete.assert_not_called()  # 仮登録行は残し、ユーザーがリトライできるようにする


def test_verify_creates_tenant_and_deletes_signup_request():
    valid_request = MagicMock(
        id="r1", email="user@example.com", tenant_name="Acme Corp", slug="acme-corp",
        password_hash="hashed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("app.routers.signup.SessionLocal") as mock_session, \
         patch("app.routers.signup.setup_tenant", return_value=("org1", "tok1", "5")), \
         patch("app.routers.signup.create_tenant_user_record") as mock_create_user, \
         patch("app.routers.signup.write_audit_log"):
        db = _session_ctx(query_results=valid_request)
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "valid-token"})
    assert resp.status_code == 200
    assert db.add.called  # 新しいTenant行
    assert mock_create_user.called
    assert db.delete.called  # tenant_signup_requests行の削除
