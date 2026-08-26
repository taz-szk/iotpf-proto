import time
from unittest.mock import patch, MagicMock
import pytest
import app.services.token_blocklist as bl
from app.services.auth import create_refresh_token, verify_token


@pytest.fixture(autouse=True)
def clear_blocklist():
    bl._revoked.clear()
    yield
    bl._revoked.clear()


def _mock_user(token_version: int = 1):
    u = MagicMock()
    u.id = "11111111-1111-1111-1111-111111111111"
    u.email = "admin@example.com"
    u.is_active = True
    u.token_version = token_version
    return u


# ---------- token_blocklist unit tests ----------

def test_not_revoked_initially():
    assert not bl.is_revoked("some-jti")


def test_revoke_and_check():
    bl.revoke_jti("jti-abc", 60.0)
    assert bl.is_revoked("jti-abc")


def test_expired_entry_evicted():
    bl.revoke_jti("jti-old", 0.001)
    time.sleep(0.05)
    assert not bl.is_revoked("jti-old")


# ---------- refresh token contains jti ----------

def test_refresh_token_has_jti():
    token = create_refresh_token({"sub": "u1", "email": "a@b.com", "type": "platform", "tok_ver": 1})
    payload = verify_token(token)
    assert "jti" in payload
    assert payload["jti"]  # non-empty


def test_each_refresh_token_unique_jti():
    data = {"sub": "u1", "email": "a@b.com", "type": "platform", "tok_ver": 1}
    t1 = create_refresh_token(data)
    t2 = create_refresh_token(data)
    p1 = verify_token(t1)
    p2 = verify_token(t2)
    assert p1["jti"] != p2["jti"]


# ---------- /auth/refresh endpoint tests ----------

def test_refresh_valid_token(client):
    user = _mock_user(token_version=1)
    token = create_refresh_token({"sub": str(user.id), "email": user.email, "type": "platform", "tok_ver": 1})

    with patch("app.routers.auth.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = user

        resp = client.post("/auth/refresh", json={"refresh_token": token})

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_refresh_revoked_token_rejected(client):
    user = _mock_user(token_version=1)
    token = create_refresh_token({"sub": str(user.id), "email": user.email, "type": "platform", "tok_ver": 1})
    payload = verify_token(token)
    bl.revoke_jti(payload["jti"], 3600.0)

    resp = client.post("/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401


def test_refresh_rotates_jti(client):
    """リフレッシュ後、使用した JTI はブロックリストに追加される。"""
    user = _mock_user(token_version=1)
    token = create_refresh_token({"sub": str(user.id), "email": user.email, "type": "platform", "tok_ver": 1})
    old_jti = verify_token(token)["jti"]

    with patch("app.routers.auth.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = user

        resp = client.post("/auth/refresh", json={"refresh_token": token})

    assert resp.status_code == 200
    assert bl.is_revoked(old_jti)

    # 旧トークンの再使用は拒否される
    resp2 = client.post("/auth/refresh", json={"refresh_token": token})
    assert resp2.status_code == 401


def test_refresh_wrong_token_version_rejected(client):
    """パスワード変更後 (token_version が変わった) のトークンは拒否される。"""
    user_v2 = _mock_user(token_version=2)
    token = create_refresh_token({"sub": str(user_v2.id), "email": user_v2.email, "type": "platform", "tok_ver": 1})

    with patch("app.routers.auth.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = user_v2

        resp = client.post("/auth/refresh", json={"refresh_token": token})

    assert resp.status_code == 401


# ---------- /auth/logout endpoint tests ----------

def test_logout_revokes_refresh_token(client):
    token = create_refresh_token({"sub": "u1", "email": "a@b.com", "type": "platform", "tok_ver": 1})
    jti = verify_token(token)["jti"]

    resp = client.post("/auth/logout", json={"refresh_token": token})
    assert resp.status_code == 204
    assert bl.is_revoked(jti)


def test_logout_without_token_succeeds(client):
    resp = client.post("/auth/logout", json={})
    assert resp.status_code == 204


def test_logout_clears_cookie(client):
    resp = client.post("/auth/logout", json={})
    assert resp.status_code == 204
    # Set-Cookie で iot_token が空またはMax-Age=0 でクリアされる
    cookie_header = resp.headers.get("set-cookie", "")
    assert "iot_token" in cookie_header
