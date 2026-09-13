from unittest.mock import MagicMock, patch

from app.services.rag_tools.tenant import TENANT_TOOLS


def test_all_tenant_tools_have_required_fields():
    for tool in TENANT_TOOLS:
        assert tool.name
        assert tool.description
        assert isinstance(tool.input_schema, dict)
        assert isinstance(tool.read_only, bool)
        assert callable(tool.handler)


def test_tenant_tool_names_are_unique_and_prefixed():
    names = [t.name for t in TENANT_TOOLS]
    assert len(names) == len(set(names))
    assert all(n.startswith("tenant_") for n in names)


def test_tenant_list_tool_is_not_present():
    """全テナント一覧に相当するツールは越境漏洩リスクのため絶対に含めない。"""
    names = [t.name for t in TENANT_TOOLS]
    assert not any("tenant_list" == n or n.endswith("_tenant_list") for n in names)


def test_action_tools_are_marked_not_read_only():
    action_names = {"tenant_provisioning_token_issue", "tenant_dashboard_panel_config_set", "tenant_alert_rule_create"}
    for tool in TENANT_TOOLS:
        if tool.name in action_names:
            assert tool.read_only is False


def test_read_only_tools_are_marked_read_only():
    read_only_names = {"tenant_provisioning_token_list", "tenant_dashboard_panel_config_list", "tenant_alert_rule_list"}
    for tool in TENANT_TOOLS:
        if tool.name in read_only_names:
            assert tool.read_only is True


def test_provisioning_token_issue_wraps_tenant_self_service_api():
    from app.services.rag_tools.tenant.provisioning import issue_provisioning_token

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.provisioning.create_token") as mock_create:
        mock_create.return_value = {"id": "token-1"}
        result = issue_provisioning_token(tenant_id="tenant-1", payload=fake_payload)

    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs["payload"] == fake_payload
    assert call_args.kwargs["body"].max_devices == 100
    assert result == {"id": "token-1"}


def test_provisioning_token_issue_omits_none_fields_to_avoid_sentinel():
    """max_devices/expires_days省略時にNoneを明示的にTokenCreateへ渡さない(番兵値踏み抜き防止)。"""
    from app.services.rag_tools.tenant.provisioning import issue_provisioning_token

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.provisioning.create_token") as mock_create:
        mock_create.return_value = {"id": "token-1"}
        issue_provisioning_token(tenant_id="tenant-1", payload=fake_payload, expires_days=30)

    body = mock_create.call_args.kwargs["body"]
    assert body.max_devices == 100  # TokenCreate自身の既定値
    assert body.expires_days == 30


def test_dashboard_panel_config_set_merges_with_existing_configs():
    from app.services.rag_tools.tenant.dashboard import set_dashboard_panel_config

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.dashboard.get_panel_configs", return_value=[{"sensor_key": "humidity", "panel_type": "gauge"}]), \
         patch("app.services.rag_tools.tenant.dashboard.put_panel_configs") as mock_put:
        set_dashboard_panel_config(tenant_id="tenant-1", payload=fake_payload, sensor_key="temperature", panel_type="timeseries")

    mock_put.assert_called_once()
    call_args = mock_put.call_args
    assert call_args.kwargs["payload"] == fake_payload
    items_by_key = {i.sensor_key: i.panel_type for i in call_args.kwargs["items"]}
    assert items_by_key["temperature"] == "timeseries"
    assert items_by_key["humidity"] == "gauge"


def test_alert_rule_create_wraps_tenant_self_service_api():
    from app.services.rag_tools.tenant.alerts import create_alert_rule_tool

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.alerts.create_alert_rule") as mock_create:
        mock_create.return_value = {"id": "rule-1"}
        result = create_alert_rule_tool(tenant_id="tenant-1", payload=fake_payload, sensor_key="temperature", condition="above", threshold=80.0)

    call_args = mock_create.call_args
    assert call_args.kwargs["payload"] == fake_payload
    assert call_args.kwargs["body"].sensor_key == "temperature"
    assert call_args.kwargs["body"].condition == "above"
    assert call_args.kwargs["body"].threshold == 80.0
    assert result == {"id": "rule-1"}
