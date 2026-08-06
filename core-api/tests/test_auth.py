from unittest.mock import patch, MagicMock
from app.services.auth import create_access_token, verify_token, hash_password, verify_password

def test_hash_and_verify_password():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed)
    assert not verify_password("wrong", hashed)

def test_create_and_verify_access_token():
    token = create_access_token({"sub": "user@example.com", "type": "platform"})
    payload = verify_token(token)
    assert payload["sub"] == "user@example.com"
    assert payload["type"] == "platform"

def test_verify_token_invalid():
    payload = verify_token("invalid.token.here")
    assert payload is None

def test_login_success(client):
    mock_user = MagicMock()
    mock_user.id = "11111111-1111-1111-1111-111111111111"
    mock_user.email = "admin@example.com"
    mock_user.is_active = True

    with patch("app.routers.auth.SessionLocal") as mock_session, \
         patch("app.routers.auth.verify_password", return_value=True):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        resp = client.post("/auth/login", json={
            "email": "admin@example.com",
            "password": "secret"
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data

def test_login_wrong_password(client):
    mock_user = MagicMock()
    mock_user.is_active = True

    with patch("app.routers.auth.SessionLocal") as mock_session, \
         patch("app.routers.auth.verify_password", return_value=False):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        resp = client.post("/auth/login", json={
            "email": "admin@example.com",
            "password": "wrong"
        })

    assert resp.status_code == 401
