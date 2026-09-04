"""公開ダッシュボードトークンがtenant-portal APIにアクセスできないことを確認する回帰テスト。"""
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _public_viewer_jwt():
    """public_access.py が /public/{token} で発行するJWTを模擬する。"""
    return create_access_token({
        "sub": f"public:{_TENANT_ID}",
        "email": "viewer-public",
        "type": "tenant",
        "role": "viewer",
        "tenant_id": _TENANT_ID,
        "public": True,
    })


def _real_tenant_jwt(role="viewer"):
    """通常ログインで発行されるJWTを模擬する（publicクレームなし）。"""
    return create_access_token({
        "sub": "real-user-id",
        "email": "user@acme.com",
        "type": "tenant",
        "role": role,
        "tenant_id": _TENANT_ID,
    })


def test_public_viewer_token_cannot_list_provisioning_tokens():
    resp = client.get("/tenant-portal/me/tokens", cookies={"iot_token": _public_viewer_jwt()})
    assert resp.status_code == 401


def test_public_viewer_token_cannot_list_users():
    resp = client.get("/tenant-portal/me/users", cookies={"iot_token": _public_viewer_jwt()})
    assert resp.status_code == 401


def test_public_viewer_token_cannot_list_devices():
    resp = client.get("/tenant-portal/me/devices", cookies={"iot_token": _public_viewer_jwt()})
    assert resp.status_code == 401


def test_real_tenant_token_still_authenticates():
    # public クレームを持たない通常のテナントJWTは引き続き _require_tenant を通過できる
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = []

        resp = client.get("/tenant-portal/me/tokens", cookies={"iot_token": _real_tenant_jwt()})

    assert resp.status_code == 200
    assert resp.json() == []
