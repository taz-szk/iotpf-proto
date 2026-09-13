from app.routers.tenant_portal import AlertRuleCreate, create_alert_rule, list_alert_rules
from app.services.rag_tools import AgentTool


def create_alert_rule_tool(
    tenant_id: str, payload: dict, sensor_key: str, condition: str, threshold: float | None = None,
    severity: str = "warning", notify_emails: list[str] | None = None,
) -> dict:
    return create_alert_rule(
        body=AlertRuleCreate(
            sensor_key=sensor_key, condition=condition, threshold=threshold,
            severity=severity, notify_emails=notify_emails or [],
        ),
        payload=payload,
    )


def list_my_alert_rules(tenant_id: str, payload: dict) -> list[dict]:
    return list_alert_rules(payload=payload)


TOOLS = [
    AgentTool(
        name="tenant_alert_rule_create",
        description="自テナントにアラートルールを作成する。センサー値が条件を満たしたときに通知する。",
        input_schema={
            "type": "object",
            "properties": {
                "sensor_key": {"type": "string", "description": "対象のセンサーキー（例: temperature）"},
                "condition": {"type": "string", "enum": ["above", "below", "equal", "device_offline"]},
                "threshold": {"type": "number", "description": "しきい値（device_offline以外で必須）"},
                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                "notify_emails": {"type": "array", "items": {"type": "string"}, "description": "通知先メールアドレス"},
            },
            "required": ["sensor_key", "condition"],
        },
        read_only=False,
        handler=create_alert_rule_tool,
    ),
    AgentTool(
        name="tenant_alert_rule_list",
        description="自テナントの現在のアラートルール一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_my_alert_rules,
    ),
]
