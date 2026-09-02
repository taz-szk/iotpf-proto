from unittest.mock import patch, MagicMock
from app.services.grafana import (
    create_grafana_org, setup_grafana_datasource, create_default_dashboard,
    build_sensor_panel, build_dashboard_panels, PANEL_DATA_MODE
)

def _mock_resp(status=200, json_data=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data or {}
    m.raise_for_status = MagicMock()
    return m

def test_create_grafana_org_returns_org_id():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200, {"orgId": 42, "message": "Organization created"})
        result = create_grafana_org("test-tenant")
    assert result == 42
    mock_httpx.post.assert_called_once()

def test_setup_grafana_datasource_calls_api():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200, {"id": 1, "message": "Datasource added"})
        setup_grafana_datasource(
            org_id=42,
            tenant_name="test-tenant",
            influxdb_org_id="org-001",
            influxdb_token="token-001",
        )
    assert mock_httpx.post.called

def test_create_default_dashboard_calls_api():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200, {"id": 1, "uid": "abc", "url": "/d/abc"})
        create_default_dashboard(org_id=42, tenant_name="test-tenant")
    assert mock_httpx.post.called


def test_panel_data_mode_has_all_types():
    expected = {"timeseries", "barchart", "histogram", "heatmap", "state-timeline",
                "gauge", "stat", "bargauge", "table"}
    assert set(PANEL_DATA_MODE.keys()) == expected

def test_build_sensor_panel_timeseries():
    panel = build_sensor_panel("temperature", "timeseries", 10, 6, 1)
    assert panel["type"] == "timeseries"
    assert panel["id"] == 10
    assert panel["title"] == "temperature"
    assert panel["gridPos"] == {"x": 6, "y": 1, "w": 9, "h": 6}
    assert "aggregateWindow" in panel["targets"][0]["query"]

def test_build_sensor_panel_gauge_uses_last_query():
    panel = build_sensor_panel("humidity", "gauge", 11, 15, 1)
    assert panel["type"] == "gauge"
    assert "last()" in panel["targets"][0]["query"]
    assert "aggregateWindow" not in panel["targets"][0]["query"]

def test_build_sensor_panel_bargauge_uses_last_query():
    panel = build_sensor_panel("pressure", "bargauge", 12, 6, 8)
    assert panel["type"] == "bargauge"
    assert "last()" in panel["targets"][0]["query"]

def test_build_dashboard_panels_empty_configs_returns_fallback():
    panels = build_dashboard_panels([])
    types = [p["type"] for p in panels]
    assert "row" in types
    assert "timeseries" in types
    # should have the all-fields timeseries panel (id=3)
    ts_panel = next(p for p in panels if p["type"] == "timeseries")
    assert ts_panel["id"] == 3

def test_build_dashboard_panels_with_configs():
    configs = [
        {"sensor_key": "temperature", "panel_type": "gauge"},
        {"sensor_key": "humidity", "panel_type": "barchart"},
    ]
    panels = build_dashboard_panels(configs)
    types = [p["type"] for p in panels]
    # fallback timeseries should NOT appear
    assert not any(p.get("id") == 3 for p in panels)
    assert "gauge" in types
    assert "barchart" in types
    # fixed panels still present
    assert any(p.get("id") == 1 for p in panels)  # row
    assert any(p.get("id") == 2 for p in panels)  # stat deleted
    assert any(p.get("id") == 4 for p in panels)  # stat status

def test_build_sensor_panel_sensor_key_escaped():
    panel = build_sensor_panel('temp"test', "timeseries", 10, 6, 1)
    assert '\\"' in panel["targets"][0]["query"]
