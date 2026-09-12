from app.routers.tenant_portal import PanelConfigItem, get_panel_configs, put_panel_configs
from app.services.rag_tools import AgentTool


def _tenant_admin_payload(tenant_id: str) -> dict:
    return {"tenant_id": tenant_id, "sub": "assistant", "email": "assistant@platform", "role": "admin", "type": "tenant"}


def set_dashboard_panel_config(tenant_id: str, sensor_key: str, panel_type: str) -> dict:
    existing = get_panel_configs(group_id=None, payload=_tenant_admin_payload(tenant_id))
    merged = {item["sensor_key"]: item["panel_type"] for item in existing}
    merged[sensor_key] = panel_type
    items = [PanelConfigItem(sensor_key=k, panel_type=v) for k, v in merged.items()]
    put_panel_configs(
        items=items,
        group_id=None,
        payload=_tenant_admin_payload(tenant_id),
    )
    return {"sensor_key": sensor_key, "panel_type": panel_type}


def list_dashboard_panel_configs(tenant_id: str) -> list[dict]:
    return get_panel_configs(group_id=None, payload=_tenant_admin_payload(tenant_id))


TOOLS = [
    AgentTool(
        name="dashboard_panel_config_set",
        description="テナントのダッシュボードで、指定センサーの表示形式（グラフ種別）を設定する。",
        input_schema={
            "type": "object",
            "properties": {
                "sensor_key": {"type": "string"},
                "panel_type": {
                    "type": "string",
                    "enum": ["timeseries", "barchart", "histogram", "heatmap", "state-timeline", "gauge", "stat", "bargauge", "table"],
                },
            },
            "required": ["sensor_key", "panel_type"],
        },
        read_only=False,
        handler=set_dashboard_panel_config,
    ),
    AgentTool(
        name="dashboard_panel_config_list",
        description="テナントの現在のダッシュボード表示設定一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_dashboard_panel_configs,
    ),
]
