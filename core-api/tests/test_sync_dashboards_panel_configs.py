"""ダッシュボード再同期(sync_tenant_dashboard)が、テナントが保存したセンサー別パネル設定を
初期レイアウトで上書きして消してしまわないことの回帰テスト。"""
import uuid
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.main import app
from app.services.grafana import sync_tenant_dashboard
from app.services.panel_configs import get_default_panel_configs


def _mock_resp(status=200, json_data=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data or {}
    m.raise_for_status = MagicMock()
    return m


def _sync(**kwargs):
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.get.side_effect = [
            _mock_resp(200, {"homeDashboardUID": "uid1"}),
            _mock_resp(200, {"dashboard": {"version": 3}}),
        ]
        mock_httpx.post.return_value = _mock_resp(200, {"uid": "uid1"})
        sync_tenant_dashboard(42, "acme", [], **kwargs)
    return mock_httpx.post.call_args.kwargs["json"]["dashboard"]["panels"]


def test_sync_tenant_dashboard_applies_saved_sensor_panel_configs():
    panels = _sync(configs=[
        {"sensor_key": "temperature", "panel_type": "bargauge"},
        {"sensor_key": "humidity", "panel_type": "table"},
    ])
    by_title = {p["title"]: p["type"] for p in panels if "title" in p}
    assert by_title["temperature"] == "bargauge"
    assert by_title["humidity"] == "table"


def test_sync_tenant_dashboard_without_configs_keeps_default_layout():
    panels = _sync()
    assert any(p.get("id") == 3 and p["type"] == "timeseries" for p in panels)


def test_get_default_panel_configs_returns_sensor_key_and_panel_type():
    rows = [MagicMock(sensor_key="CO2", panel_type="barchart"),
            MagicMock(sensor_key="temperature", panel_type="bargauge")]
    with patch("app.services.panel_configs.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = rows
        result = get_default_panel_configs(str(uuid.uuid4()))
    assert result == [
        {"sensor_key": "CO2", "panel_type": "barchart"},
        {"sensor_key": "temperature", "panel_type": "bargauge"},
    ]


def test_platform_sync_dashboards_passes_saved_configs_to_each_tenant():
    tenant = MagicMock()
    tenant.id = uuid.uuid4()
    tenant.name = "acme"
    tenant.grafana_org_id = 7
    saved = [{"sensor_key": "temperature", "panel_type": "bargauge"}]

    with patch("app.routers.tenants.verify_token", return_value={"sub": "u", "type": "platform"}), \
         patch("app.routers.tenants.SessionLocal") as mock_sl, \
         patch("app.routers.tenants.list_groups_with_devices", return_value=[]), \
         patch("app.routers.tenants.get_default_panel_configs", return_value=saved), \
         patch("app.routers.tenants.sync_tenant_dashboard") as mock_sync, \
         patch("app.routers.tenants.get_or_create_platform_org", return_value=1), \
         patch("app.routers.tenants.sync_platform_dashboard"), \
         TestClient(app) as client:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_sl.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = [tenant]
        resp = client.post("/tenants/platform/sync-dashboards", headers={"Authorization": "Bearer x"})

    assert resp.status_code == 200
    mock_sync.assert_called_once_with(7, "acme", [], configs=saved)
