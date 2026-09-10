from unittest.mock import patch, MagicMock
from app.services.grafana import (
    create_grafana_org, setup_grafana_datasource, create_default_dashboard,
    build_sensor_panel, build_dashboard_panels, PANEL_DATA_MODE,
    _flux_string_escape, retire_device_in_influxdb,
    build_group_variable, build_templating, sync_tenant_dashboard_groups,
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

def test_flux_string_escape_neutralizes_interpolation():
    # $ 単独、および ${...} 形式のFlux文字列補間構文が有効なままにならないこと
    # (エスケープ後は必ず \$ の形になっており、素の $ は残らない)
    esc = _flux_string_escape('device" }} import "system"; system.exec(...) ${1+1}')
    assert '\\$' in esc
    assert esc.replace('\\$', '').count('$') == 0
    assert '\\"' in esc

def test_flux_string_escape_handles_backslash_before_dollar_and_quote():
    # バックスラッシュのエスケープが先に行われ、$ / " のエスケープで生成した
    # バックスラッシュが二重エスケープされないこと
    esc = _flux_string_escape('a\\b$c"d')
    assert esc == 'a\\\\b\\$c\\"d'

def test_build_group_variable_default_and_groups():
    groups = [
        {"name": "拠点A", "device_names": ["dev-1", "dev-2"]},
        {"name": "拠点B", "device_names": []},
    ]
    var = build_group_variable(groups)
    assert var["name"] == "group"
    assert var["type"] == "custom"
    values = {opt["text"]: opt["value"] for opt in var["options"]}
    assert values["全デバイス"] == ".*"
    assert values["拠点A"] == "^(dev\\-1|dev\\-2)$"
    assert values["拠点B"] == "^$"  # 空グループはどのdevice_nameにもマッチしない
    assert var["current"]["value"] == ".*"

def test_build_group_variable_escapes_regex_special_chars():
    groups = [{"name": "拠点C", "device_names": ["dev.1", "dev+2"]}]
    var = build_group_variable(groups)
    values = {opt["text"]: opt["value"] for opt in var["options"]}
    assert values["拠点C"] == "^(dev\\.1|dev\\+2)$"

def test_build_group_variable_no_groups_has_only_default():
    var = build_group_variable([])
    assert len(var["options"]) == 1
    assert var["options"][0]["value"] == ".*"

def test_build_templating_has_group_before_device_name():
    templating = build_templating([{"name": "拠点A", "device_names": ["dev-1"]}])
    names = [v["name"] for v in templating]
    assert names == ["group", "device_name"]
    device_var = next(v for v in templating if v["name"] == "device_name")
    assert "${group}" in device_var["query"]["query"]

def test_sync_tenant_dashboard_groups_replaces_templating_only():
    existing_dashboard = {
        "panels": [{"id": 1}, {"id": 2}],
        "templating": {"list": []},
        "version": 3,
        "uid": "uid1",
        "title": "テレメトリ監視 - acme",
    }
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.get.side_effect = [
            _mock_resp(200, {"homeDashboardUID": "uid1"}),
            _mock_resp(200, {"dashboard": existing_dashboard}),
        ]
        mock_httpx.post.return_value = _mock_resp(200, {"uid": "uid1"})
        sync_tenant_dashboard_groups(
            org_id=42, tenant_name="acme",
            groups=[{"name": "拠点A", "device_names": ["dev-1"]}],
        )
    posted = mock_httpx.post.call_args.kwargs["json"]
    assert posted["dashboard"]["panels"] == [{"id": 1}, {"id": 2}]
    var_names = [v["name"] for v in posted["dashboard"]["templating"]["list"]]
    assert "group" in var_names

def test_sync_tenant_dashboard_groups_skips_when_no_dashboard_yet():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.get.return_value = _mock_resp(200, {"homeDashboardUID": None})
        sync_tenant_dashboard_groups(org_id=42, tenant_name="acme", groups=[])
    mock_httpx.post.assert_not_called()

def test_retire_device_in_influxdb_escapes_dollar_in_flux_query():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200)
        retire_device_in_influxdb("org-1", 'evil" }} ${badExpr}')
    copy_call = mock_httpx.post.call_args_list[0]
    flux_query = copy_call.kwargs["json"]["query"]
    assert '\\${badExpr}' in flux_query
    assert flux_query.replace('\\$', '').count('$') == 0
