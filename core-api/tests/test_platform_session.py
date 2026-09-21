"""プラットフォーム管理者のCookie JWT(Grafana用・24時間有効)を、DBの最新状態で再検証することのテスト。"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import platform_session
from app.services.auth import create_access_token

# conftestのautouseフィクスチャが素通しに差し替えるため、実物をここで確保する
_real = platform_session.revalidate_platform_session

_USER = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture(autouse=True)
def _real_check(monkeypatch):
    monkeypatch.setattr(platform_session, "revalidate_platform_session", _real)


def _db(user):
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = user
    cm = MagicMock()
    cm.__enter__.return_value = session
    return MagicMock(return_value=cm)


def _user(active=True, token_version=3):
    u = MagicMock()
    u.is_active, u.token_version = active, token_version
    return u


def _payload(tok_ver=3):
    return {"sub": _USER, "email": "root@example.com", "type": "platform", "tok_ver": tok_ver}


def test_live_user_with_matching_token_version_passes():
    with patch("app.services.platform_session.SessionLocal", _db(_user())):
        _real(_payload())


def test_password_changed_since_login_is_rejected():
    with patch("app.services.platform_session.SessionLocal", _db(_user(token_version=4))):
        with pytest.raises(HTTPException) as e:
            _real(_payload(tok_ver=3))
    assert e.value.status_code == 401


@pytest.mark.parametrize("user", [None, _user(active=False)])
def test_missing_or_deactivated_user_is_rejected(user):
    with patch("app.services.platform_session.SessionLocal", _db(user)):
        with pytest.raises(HTTPException) as e:
            _real(_payload())
    assert e.value.status_code == 401


def test_bad_subject_is_rejected_without_db_access():
    with patch("app.services.platform_session.SessionLocal") as sl:
        with pytest.raises(HTTPException):
            _real({**_payload(), "sub": "not-a-uuid"})
    sl.assert_not_called()


def test_db_error_fails_closed():
    sl = MagicMock(side_effect=Exception("db down"))
    with patch("app.services.platform_session.SessionLocal", sl):
        with pytest.raises(HTTPException) as e:
            _real(_payload())
    assert e.value.status_code == 401


def test_grafana_verify_jwt_rejects_a_platform_cookie_after_password_change():
    token = create_access_token(_payload(tok_ver=3))
    with patch("app.services.platform_session.SessionLocal", _db(_user(token_version=4))), \
         TestClient(app) as client:
        resp = client.get("/auth/verify-jwt", cookies={"iot_token": token})
    assert resp.status_code == 401


def test_grafana_verify_jwt_accepts_a_live_platform_cookie():
    token = create_access_token(_payload(tok_ver=3))
    with patch("app.services.platform_session.SessionLocal", _db(_user(token_version=3))), \
         TestClient(app) as client:
        resp = client.get("/auth/verify-jwt", cookies={"iot_token": token})
    assert resp.status_code == 200
    assert resp.headers["X-Auth-User"] == "root@example.com"
