"""テナントポータルのユーザー管理で、operatorがadminを無効化・削除して、
テナントの管理者を締め出せないことの回帰テスト。"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import create_access_token

_TENANT = "11111111-1111-1111-1111-111111111111"
_ACTOR = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TARGET = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _jwt(role):
    return create_access_token({"sub": _ACTOR, "email": "actor@acme.com", "type": "tenant",
                                "role": role, "tenant_id": _TENANT})


def _engine(target_role, rowcount=1):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = MagicMock(role=target_role) if target_role else None
    conn.execute.return_value.rowcount = rowcount
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    return engine, conn


def _call(method, actor_role, target_role, **kw):
    engine, conn = _engine(target_role)
    with patch("app.routers.tenant_portal.engine", engine), \
         patch("app.routers.tenant_portal.log_audit"), \
         TestClient(app) as client:
        resp = client.request(method, f"/tenant-portal/me/users/{_TARGET}",
                              cookies={"iot_token": _jwt(actor_role)}, **kw)
    return resp, conn


def _wrote(conn):
    return any(("UPDATE" in str(c.args[0]) or "DELETE" in str(c.args[0])) for c in conn.execute.call_args_list)


@pytest.mark.parametrize("method,kw", [
    ("DELETE", {}),
    ("PATCH", {"json": {"is_active": False}}),
])
def test_operator_cannot_modify_admin(method, kw):
    resp, conn = _call(method, "operator", "admin", **kw)
    assert resp.status_code == 403
    assert not _wrote(conn)


@pytest.mark.parametrize("method,kw", [
    ("DELETE", {}),
    ("PATCH", {"json": {"is_active": False}}),
])
@pytest.mark.parametrize("target_role", ["viewer", "operator"])
def test_operator_can_still_manage_non_admin_users(method, kw, target_role):
    resp, conn = _call(method, "operator", target_role, **kw)
    assert resp.status_code == 204
    assert _wrote(conn)


@pytest.mark.parametrize("method,kw", [
    ("DELETE", {}),
    ("PATCH", {"json": {"is_active": False}}),
])
def test_admin_can_manage_admin(method, kw):
    resp, conn = _call(method, "admin", "admin", **kw)
    assert resp.status_code == 204
    assert _wrote(conn)


@pytest.mark.parametrize("method,kw", [
    ("DELETE", {}),
    ("PATCH", {"json": {"is_active": False}}),
])
def test_unknown_target_user_is_404(method, kw):
    resp, _ = _call(method, "operator", None, **kw)
    assert resp.status_code == 404
