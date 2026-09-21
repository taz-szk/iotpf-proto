"""テナントのセッション(Cookie JWT)を、DBの最新状態で再検証することのテスト。

JWTは最大24時間有効なため、JWTだけを見ていると、削除・無効化・降格したユーザーや
削除済みテナントが、失効までそのまま操作できてしまう。"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import tenant_session
from app.services.auth import create_access_token

# conftestのautouseフィクスチャがrevalidate_sessionを素通しに差し替えるため、実物をここで確保する
_real_revalidate = tenant_session.revalidate_session

_TENANT = "11111111-1111-1111-1111-111111111111"
_USER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_PW_HASH = "$2b$12$currentpasswordhashcurrentpasswordhashcurrentpasswordhash"
_PWV = tenant_session.password_fingerprint(_PW_HASH)


@pytest.fixture(autouse=True)
def _real_session_check(monkeypatch):
    monkeypatch.setattr(tenant_session, "revalidate_session", _real_revalidate)
    tenant_session._cache.clear()
    yield
    tenant_session._cache.clear()


def _jwt(role="admin", public=False):
    claims = {"sub": _USER, "email": "u@acme.com", "type": "tenant", "role": role, "tenant_id": _TENANT,
              "pwv": _PWV}
    if public:
        claims.update({"sub": f"public:{_TENANT}", "public": True, "role": "viewer"})
    return create_access_token(claims)


def _engine(role, pw_hash=_PW_HASH):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = MagicMock(role=role, password_hash=pw_hash) if role else None
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    return engine, conn


def test_role_comes_from_the_db_not_the_stale_jwt():
    engine, _ = _engine("viewer")
    payload = {"sub": _USER, "tenant_id": _TENANT, "role": "admin", "email": "u@acme.com", "pwv": _PWV}
    with patch("app.services.tenant_session.engine", engine):
        assert _real_revalidate(payload)["role"] == "viewer"


@pytest.mark.parametrize("bad", [
    {"sub": _USER, "tenant_id": "not-a-uuid"},
    {"sub": "public:" + _TENANT, "tenant_id": _TENANT},
    {"sub": _USER},
])
def test_malformed_identity_is_rejected_without_db_access(bad):
    with patch("app.services.tenant_session.engine") as engine:
        with pytest.raises(HTTPException) as e:
            _real_revalidate({**bad, "role": "admin"})
    assert e.value.status_code == 401
    engine.connect.assert_not_called()


def test_deleted_or_inactive_user_is_rejected():
    engine, _ = _engine(None)
    with patch("app.services.tenant_session.engine", engine):
        with pytest.raises(HTTPException) as e:
            _real_revalidate({"sub": _USER, "tenant_id": _TENANT, "role": "admin", "pwv": _PWV})
    assert e.value.status_code == 401


def test_db_error_fails_closed():
    engine = MagicMock()
    engine.connect.side_effect = Exception("db down")
    with patch("app.services.tenant_session.engine", engine):
        with pytest.raises(HTTPException) as e:
            _real_revalidate({"sub": _USER, "tenant_id": _TENANT, "role": "admin", "pwv": _PWV})
    assert e.value.status_code == 401


def test_result_is_cached_briefly_and_forget_session_drops_it():
    engine, conn = _engine("admin")
    payload = {"sub": _USER, "tenant_id": _TENANT, "role": "admin", "pwv": _PWV}
    with patch("app.services.tenant_session.engine", engine):
        _real_revalidate(payload)
        _real_revalidate(payload)
        assert conn.execute.call_count == 1
        tenant_session.forget_session(_TENANT, _USER)
        _real_revalidate(payload)
        assert conn.execute.call_count == 2


def test_portal_rejects_a_token_of_a_deleted_user():
    engine, _ = _engine(None)
    with patch("app.services.tenant_session.engine", engine), TestClient(app) as client:
        resp = client.get("/tenant-portal/me/users", cookies={"iot_token": _jwt("admin")})
    assert resp.status_code == 401


def test_portal_uses_the_current_db_role_for_authorization():
    """JWTはadminのままでも、DB上でviewerに降格されていれば管理者専用操作はできない。"""
    engine, _ = _engine("viewer")
    with patch("app.services.tenant_session.engine", engine), TestClient(app) as client:
        resp = client.post("/tenant-portal/me/users", cookies={"iot_token": _jwt("admin")},
                           json={"email": "x@acme.com", "password": "longenough1", "role": "viewer"})
    assert resp.status_code == 403


def test_tenant_auth_me_rejects_public_dashboard_token():
    with TestClient(app) as client:
        resp = client.get("/tenant-auth/me", cookies={"iot_token": _jwt(public=True)})
    assert resp.status_code == 401


def test_tenant_auth_me_works_for_a_live_user():
    engine, _ = _engine("admin")
    tenant = MagicMock()
    tenant.name, tenant.grafana_org_id = "acme", "2"
    with patch("app.services.tenant_session.engine", engine), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         TestClient(app) as client:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get("/tenant-auth/me", cookies={"iot_token": _jwt("admin")})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_broken_dead_route_tenant_auth_me_devices_is_gone():
    with TestClient(app) as client:
        resp = client.get("/tenant-auth/me/devices", cookies={"iot_token": _jwt("admin")})
    assert resp.status_code == 404


def test_grafana_verify_jwt_rejects_a_deleted_tenant_user():
    engine, _ = _engine(None)
    with patch("app.services.tenant_session.engine", engine), TestClient(app) as client:
        resp = client.get("/auth/verify-jwt", cookies={"iot_token": _jwt("admin")})
    assert resp.status_code == 401


def test_grafana_verify_jwt_still_allows_public_dashboard_token():
    with patch("app.services.tenant_session.engine") as engine, TestClient(app) as client:
        resp = client.get("/auth/verify-jwt", cookies={"iot_token": _jwt(public=True)})
    assert resp.status_code == 200
    engine.connect.assert_not_called()


# ---------- パスワード変更・リセットでセッションを失効させる ----------

def test_token_issued_before_a_password_change_is_rejected():
    engine, _ = _engine("admin", pw_hash="$2b$12$anewpasswordhashanewpasswordhashanewpasswordhashanewpassw")
    payload = {"sub": _USER, "tenant_id": _TENANT, "role": "admin", "pwv": _PWV}
    with patch("app.services.tenant_session.engine", engine):
        with pytest.raises(HTTPException) as e:
            _real_revalidate(payload)
    assert e.value.status_code == 401


def test_token_without_password_claim_is_rejected():
    engine, _ = _engine("admin")
    with patch("app.services.tenant_session.engine", engine):
        with pytest.raises(HTTPException) as e:
            _real_revalidate({"sub": _USER, "tenant_id": _TENANT, "role": "admin"})
    assert e.value.status_code == 401


def test_fingerprint_is_stable_and_does_not_contain_the_hash():
    assert tenant_session.password_fingerprint(_PW_HASH) == tenant_session.password_fingerprint(_PW_HASH)
    assert tenant_session.password_fingerprint(_PW_HASH) != tenant_session.password_fingerprint(_PW_HASH + "x")
    assert _PW_HASH[7:] not in tenant_session.password_fingerprint(_PW_HASH)


def test_change_password_keeps_the_caller_logged_in_with_a_fresh_cookie():
    """自分でパスワードを変えた本人のセッションは、新しいパスワードの指紋で発行し直して継続させる。"""
    from app.services.auth import verify_token
    new_hash = "$2b$12$brandnewhashbrandnewhashbrandnewhashbrandnewhashbrandnew"
    engine, conn = _engine("admin")
    with patch("app.services.tenant_session.engine", engine),          patch("app.routers.tenant_auth.engine", engine),          patch("app.routers.tenant_auth.verify_password", return_value=True),          patch("app.routers.tenant_auth.hash_password", return_value=new_hash),          patch("app.routers.tenant_auth.log_audit"),          TestClient(app) as client:
        resp = client.post("/tenant-auth/change-password", cookies={"iot_token": _jwt("admin")},
                           json={"current_password": "old-password-1", "new_password": "a-brand-new-password"})
    assert resp.status_code == 204
    cookie = resp.cookies.get("iot_token")
    assert cookie
    claims = verify_token(cookie)
    assert claims["pwv"] == tenant_session.password_fingerprint(new_hash)
    assert claims["sub"] == _USER and claims["tenant_id"] == _TENANT


def test_admin_password_reset_drops_the_target_users_cached_session():
    engine, _ = _engine("admin")
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.rowcount = 1
    with patch("app.services.tenant_session.engine", engine),          patch("app.routers.tenant_portal.engine", engine),          patch("app.routers.tenant_portal.forget_session") as forget,          TestClient(app) as client:
        resp = client.patch("/tenant-portal/me/users/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/password",
                            cookies={"iot_token": _jwt("admin")},
                            json={"password": "another-long-password"})
    assert resp.status_code == 204
    forget.assert_called_once_with(_TENANT, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
