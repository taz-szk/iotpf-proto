from unittest.mock import MagicMock, patch

from app.services.rag_tools import TOOLS


def test_all_tools_have_required_fields():
    for tool in TOOLS:
        assert tool.name
        assert tool.description
        assert isinstance(tool.input_schema, dict)
        assert isinstance(tool.read_only, bool)
        assert callable(tool.handler)


def test_tool_names_are_unique():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))


def test_action_tools_are_marked_not_read_only():
    action_tool_names = {"provisioning_token_issue", "dashboard_panel_config_set", "alert_rule_create"}
    for tool in TOOLS:
        if tool.name in action_tool_names:
            assert tool.read_only is False


def test_read_only_tools_are_marked_read_only():
    read_only_names = {
        "tenant_list", "tenant_stats_get", "tenant_invoice_get",
        "provisioning_token_list", "dashboard_panel_config_list", "alert_rule_list",
    }
    for tool in TOOLS:
        if tool.name in read_only_names:
            assert tool.read_only is True


def test_provisioning_token_issue_calls_existing_endpoint():
    from app.services.rag_tools.provisioning import issue_provisioning_token

    with patch("app.services.rag_tools.provisioning.create_provisioning_token") as mock_create:
        mock_create.return_value = MagicMock(id="token-1")
        issue_provisioning_token(tenant_id="tenant-1", max_devices=10, expires_days=365)

    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs["tenant_id"] == "tenant-1"
    assert call_args.kwargs["body"].max_devices == 10
    assert call_args.kwargs["payload"].get("role") is None or call_args.kwargs["payload"].get("type") == "platform"


def test_provisioning_token_issue_uses_default_values_when_args_omitted():
    """引数を省略した場合、TokenCreateの既定値(100台/365日)が使われ、
    Noneを明示的に渡して番兵値(無制限)を踏み抜かないことを検証する。"""
    from app.services.rag_tools.provisioning import issue_provisioning_token

    with patch("app.services.rag_tools.provisioning.create_provisioning_token") as mock_create:
        mock_create.return_value = MagicMock(id="token-1")
        issue_provisioning_token(tenant_id="tenant-1")

    mock_create.assert_called_once()
    body = mock_create.call_args.kwargs["body"]
    assert body.max_devices == 100
    assert body.expires_days == 365


def test_alert_rule_create_calls_existing_endpoint():
    from app.services.rag_tools.alerts import create_alert_rule_tool

    with patch("app.services.rag_tools.alerts.create_alert_rule") as mock_create:
        create_alert_rule_tool(
            tenant_id="tenant-1", sensor_key="temperature", condition="above", threshold=80.0,
        )

    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs["tenant_id"] == "tenant-1"
    assert call_args.kwargs["body"].sensor_key == "temperature"
    assert call_args.kwargs["body"].condition == "above"
    assert call_args.kwargs["body"].threshold == 80.0


def test_dashboard_panel_config_set_calls_existing_endpoint_with_synthetic_tenant_payload():
    from app.services.rag_tools.dashboard import set_dashboard_panel_config

    with patch("app.services.rag_tools.dashboard.get_panel_configs", return_value=[{"sensor_key": "humidity", "panel_type": "gauge"}]), \
         patch("app.services.rag_tools.dashboard.put_panel_configs") as mock_put:
        set_dashboard_panel_config(tenant_id="tenant-1", sensor_key="temperature", panel_type="timeseries")

    mock_put.assert_called_once()
    call_args = mock_put.call_args
    assert call_args.kwargs["payload"]["tenant_id"] == "tenant-1"
    assert call_args.kwargs["payload"]["role"] == "admin"
    items = call_args.kwargs["items"]
    items_by_key = {i.sensor_key: i.panel_type for i in items}
    assert items_by_key["temperature"] == "timeseries"
    assert items_by_key["humidity"] == "gauge"
