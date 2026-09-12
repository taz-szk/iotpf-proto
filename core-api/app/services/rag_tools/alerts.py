from app.routers.alert_rules import AlertRuleCreate, create_alert_rule, list_alert_rules
from app.services.rag_tools import AgentTool

_SYSTEM_PAYLOAD = {"sub": "00000000-0000-0000-0000-000000000000", "email": "assistant@platform", "type": "platform"}


def create_alert_rule_tool(
    tenant_id: str, sensor_key: str, condition: str, threshold: float | None = None,
    severity: str = "warning", notify_emails: list[str] | None = None,
) -> dict:
    rule = create_alert_rule(
        tenant_id=tenant_id,
        body=AlertRuleCreate(
            sensor_key=sensor_key, condition=condition, threshold=threshold,
            severity=severity, notify_emails=notify_emails or [],
        ),
        _=_SYSTEM_PAYLOAD,
    )
    return rule.model_dump() if hasattr(rule, "model_dump") else dict(rule)


def list_tenant_alert_rules(tenant_id: str) -> list[dict]:
    rules = list_alert_rules(tenant_id=tenant_id, _=_SYSTEM_PAYLOAD)
    return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rules]


TOOLS = [
    AgentTool(
        name="alert_rule_create",
        description="テナントにアラートルールを作成する。センサー値が条件を満たしたときに通知する。",
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
        name="alert_rule_list",
        description="テナントの現在のアラートルール一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_tenant_alert_rules,
    ),
]
