from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.auth import create_access_token, create_refresh_token

client = TestClient(app)

def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})

def _make_tenant(tid="33333333-3333-3333-3333-333333333333"):
    t = MagicMock()
    t.id = tid
    t.status = "active"
    return t

def test_list_tenant_devices_empty():
    tenant = _make_tenant()
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        resp = client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == []

def test_list_tenant_devices_returns_rows():
    from datetime import datetime, timezone
    tenant = _make_tenant()
    now = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
    mock_row = MagicMock()
    mock_row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    mock_row.device_id = "device-001"
    mock_row.device_name = "Device 001"
    mock_row.group_id = None
    mock_row.connection_status = "online"
    mock_row.last_seen_at = now
    mock_row.fw_version = "1.2.3"
    mock_row.cert_not_after = None
    mock_row.created_at = now
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        resp = client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["device_id"] == "device-001"
    assert data[0]["device_name"] == "Device 001"
    assert data[0]["connection_status"] == "online"
    assert data[0]["fw_version"] == "1.2.3"
    assert data[0]["cert_not_after"] is None
    assert data[0]["group_id"] is None

def test_list_tenant_devices_unauthorized():
    resp = client.get("/tenants/33333333-3333-3333-3333-333333333333/devices")
    assert resp.status_code == 401

def test_list_tenant_devices_schema_name():
    """スキーマ名変換（UUID のダッシュ→アンダースコア）を検証する"""
    tenant = _make_tenant()
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    sql = str(mock_conn.execute.call_args[0][0])
    assert '"tenant_33333333_3333_3333_3333_333333333333".devices' in sql

def test_list_tenant_devices_not_found():
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404

def test_list_tenant_devices_refresh_token_rejected():
    """refresh トークンは 401 を返す"""
    token = create_refresh_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})
    resp = client.get(
        "/tenants/33333333-3333-3333-3333-333333333333/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401

def test_list_tenant_devices_tenant_token_rejected():
    """tenant 型トークンは 401 を返す"""
    token = create_access_token({"sub": "u-id", "email": "u@tenant.local", "type": "tenant", "tenant_id": "33333333-3333-3333-3333-333333333333"})
    resp = client.get(
        "/tenants/33333333-3333-3333-3333-333333333333/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401

def test_delete_device_also_removes_its_sim_binding():
    """デバイス削除時に sim_bindings の紐づけも削除することを検証"""
    TENANT_ID = "33333333-3333-3333-3333-333333333333"
    tenant = _make_tenant(TENANT_ID)
    tenant.influxdb_org_id = None

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = MagicMock(device_name="センサー01")

    def mock_engine_factory(device_name="Device 001"):
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        return engine

    with patch("app.routers.tenant_devices._get_active_tenant", return_value=tenant), \
         patch("app.routers.tenant_devices.engine", mock_engine_factory()), \
         patch("app.routers.tenant_devices.forget_device"), \
         patch("app.routers.tenant_devices.kick_client"), \
         patch("app.routers.tenant_devices.retire_device_in_influxdb"), \
         patch("app.routers.tenant_devices.log_audit"), \
         patch("app.routers.tenant_devices.ensure_sim_tables_to_tenant_schema") as mock_ensure:
        resp = client.delete(
            f"/tenants/{TENANT_ID}/devices/dev-001",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 204
    mock_ensure.assert_called_once_with(TENANT_ID)
    sql_statements = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("sim_bindings" in s and "DELETE" in s for s in sql_statements)
