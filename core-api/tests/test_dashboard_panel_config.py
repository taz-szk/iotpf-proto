from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _tenant_token(role: str = "admin"):
    return create_access_token({
        "sub": "user-id",
        "email": "user@test.com",
        "type": "tenant",
        "tenant_id": TENANT_ID,
        "role": role,
    })

def _make_tenant():
    t = MagicMock()
    t.id = TENANT_ID
    t.name = "test-tenant"
    t.grafana_org_id = "5"
    return t

def _make_config(sensor_key: str, panel_type: str):
    c = MagicMock()
    c.sensor_key = sensor_key
    c.panel_type = panel_type
    return c

# ─── GET ────────────────────────────────────────────────────────────────────

def test_get_panel_configs_returns_empty_list():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.all.return_value = []
        resp = client.get(
            "/tenant-portal/dashboard/panel-configs",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    assert resp.json() == []

def test_get_panel_configs_returns_existing_configs():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.all.return_value = [
            _make_config("temperature", "gauge"),
            _make_config("humidity", "timeseries"),
        ]
        resp = client.get(
            "/tenant-portal/dashboard/panel-configs",
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["sensor_key"] == "temperature"
    assert data[0]["panel_type"] == "gauge"

def test_get_panel_configs_requires_auth():
    resp = client.get("/tenant-portal/dashboard/panel-configs")
    assert resp.status_code == 401

# ─── PUT ────────────────────────────────────────────────────────────────────

def test_put_panel_configs_success_operator():
    tenant = _make_tenant()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.services.grafana.sync_tenant_dashboard_with_configs") as mock_sync:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/dashboard/panel-configs",
            json=[{"sensor_key": "temperature", "panel_type": "gauge"}],
            cookies={"iot_token": _tenant_token("operator")},
        )
    assert resp.status_code == 204
    mock_sync.assert_called_once_with(5, "test-tenant", [{"sensor_key": "temperature", "panel_type": "gauge"}])

def test_put_panel_configs_viewer_is_forbidden():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[{"sensor_key": "temperature", "panel_type": "gauge"}],
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403

def test_put_panel_configs_invalid_sensor_key():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[{"sensor_key": "temp/erature", "panel_type": "gauge"}],
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422

def test_put_panel_configs_invalid_panel_type():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[{"sensor_key": "temperature", "panel_type": "piechart"}],
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422

def test_put_panel_configs_empty_list_clears_configs():
    tenant = _make_tenant()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.services.grafana.sync_tenant_dashboard_with_configs") as mock_sync:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/dashboard/panel-configs",
            json=[],
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 204
    mock_sync.assert_called_once_with(5, "test-tenant", [])

def test_put_panel_configs_requires_auth():
    resp = client.put("/tenant-portal/dashboard/panel-configs", json=[])
    assert resp.status_code == 401

def test_put_panel_configs_duplicate_sensor_key():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[
            {"sensor_key": "temperature", "panel_type": "gauge"},
            {"sensor_key": "temperature", "panel_type": "timeseries"},
        ],
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422
