"""デバイス削除時に、接続中のMQTTセッションを切断し、登録確認のキャッシュを破棄することの回帰テスト。
証明書が有効なままだと、削除したデバイスが接続・送信を続けられてしまうため。"""
import uuid
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import create_access_token

_TENANT = "11111111-1111-1111-1111-111111111111"


def _mock_engine(device_name="Device 001"):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = MagicMock(device_name=device_name)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    return engine


def _tenant_row():
    t = MagicMock()
    t.influxdb_org_id = "org-1"
    return t


def test_platform_delete_device_kicks_client_and_forgets_cache():
    with patch("app.routers.tenant_devices.verify_token", return_value={"sub": str(uuid.uuid4()), "type": "platform", "token_type": "access", "email": "a@b.c"}), \
         patch("app.routers.tenant_devices.engine", _mock_engine()), \
         patch("app.routers.tenant_devices._get_active_tenant", return_value=_tenant_row()), \
         patch("app.routers.tenant_devices.retire_device_in_influxdb"), \
         patch("app.routers.tenant_devices.log_audit"), \
         patch("app.routers.tenant_devices.ensure_sim_tables_to_tenant_schema"), \
         patch("app.routers.tenant_devices.kick_client") as mock_kick, \
         patch("app.routers.tenant_devices.forget_device") as mock_forget, \
         TestClient(app) as client:
        resp = client.delete(f"/tenants/{_TENANT}/devices/dev-001", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 204
    mock_kick.assert_called_once_with(f"{_TENANT}:dev-001")
    mock_forget.assert_called_once_with(_TENANT, "dev-001")


def test_portal_delete_device_kicks_client_and_forgets_cache():
    jwt = create_access_token({"sub": "u1", "email": "a@acme.com", "type": "tenant", "role": "admin", "tenant_id": _TENANT})
    with patch("app.routers.tenant_portal.engine", _mock_engine()), \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.retire_device_in_influxdb"), \
         patch("app.routers.tenant_portal.log_audit"), \
         patch("app.routers.tenant_portal.ensure_sim_tables_to_tenant_schema"), \
         patch("app.routers.tenant_portal.kick_client") as mock_kick, \
         patch("app.routers.tenant_portal.forget_device") as mock_forget, \
         TestClient(app) as client:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _tenant_row()
        resp = client.delete("/tenant-portal/me/devices/dev-001", cookies={"iot_token": jwt})
    assert resp.status_code == 204
    mock_kick.assert_called_once_with(f"{_TENANT}:dev-001")
    mock_forget.assert_called_once_with(_TENANT, "dev-001")


def test_portal_delete_device_also_removes_its_sim_binding():
    """テナントポータル: デバイス削除時に sim_bindings の紐づけも削除することを検証"""
    jwt = create_access_token({"sub": "u1", "email": "a@acme.com", "type": "tenant", "role": "admin", "tenant_id": _TENANT})
    engine_mock = _mock_engine()
    with patch("app.routers.tenant_portal.engine", engine_mock), \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.retire_device_in_influxdb"), \
         patch("app.routers.tenant_portal.log_audit"), \
         patch("app.routers.tenant_portal.kick_client"), \
         patch("app.routers.tenant_portal.forget_device"), \
         patch("app.routers.tenant_portal.ensure_sim_tables_to_tenant_schema") as mock_ensure, \
         TestClient(app) as client:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = _tenant_row()
        resp = client.delete("/tenant-portal/me/devices/dev-001", cookies={"iot_token": jwt})

    assert resp.status_code == 204
    mock_ensure.assert_called_once_with(_TENANT)
    # Check that DELETE FROM sim_bindings was executed
    conn = engine_mock.connect.return_value.__enter__.return_value
    sql_statements = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("sim_bindings" in s and "DELETE" in s for s in sql_statements)


def test_list_devices_includes_bound_sim_imsi():
    """テナントポータル: list_devices が sim_imsi を返すことを検証"""
    jwt = create_access_token({"sub": "u1", "email": "a@acme.com", "type": "tenant", "role": "viewer", "tenant_id": _TENANT})
    with patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.ensure_sim_tables_to_tenant_schema"), \
         TestClient(app) as client:
        conn = MagicMock()
        conn.__enter__ = lambda s: conn
        conn.__exit__ = MagicMock(return_value=False)
        row = MagicMock(
            id="d1", device_id="dev-001", device_name="センサー01", connection_status="online",
            last_seen_at=None, fw_version=None, cert_not_after=None, created_at=None,
            group_id=None, sim_imsi="440100000001"
        )
        conn.execute.return_value.fetchall.return_value = [row]
        mock_engine.connect.return_value = conn

        resp = client.get("/tenant-portal/me/devices", cookies={"iot_token": jwt})
    assert resp.status_code == 200
    assert resp.json()[0]["sim_imsi"] == "440100000001"
