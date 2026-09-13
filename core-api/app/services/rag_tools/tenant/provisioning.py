from app.routers.tenant_portal import TokenCreate, create_token, list_tokens
from app.services.rag_tools import AgentTool


def issue_provisioning_token(tenant_id: str, payload: dict, max_devices: int | None = None, expires_days: int | None = None) -> dict:
    # TokenCreateはmax_devicesが常にint必須(Noneにできない)なので番兵値の心配はないが、
    # expires_daysは省略時にNoneを明示的に渡すと無期限番兵値を踏み抜く。指定分のみ渡す。
    body_kwargs: dict = {}
    if max_devices is not None:
        body_kwargs["max_devices"] = max_devices
    if expires_days is not None:
        body_kwargs["expires_days"] = expires_days
    return create_token(body=TokenCreate(**body_kwargs), payload=payload)


def list_my_provisioning_tokens(tenant_id: str, payload: dict) -> list[dict]:
    return list_tokens(payload=payload)


TOOLS = [
    AgentTool(
        name="tenant_provisioning_token_issue",
        description="自テナントの新しいプロビジョニングトークンを発行する。デバイスをプラットフォームに登録するために必要。",
        input_schema={
            "type": "object",
            "properties": {
                "max_devices": {"type": "integer", "description": "登録可能な最大デバイス数（省略時は100）"},
                "expires_days": {"type": "integer", "description": "有効期限（日数、省略時は365日。無期限にはできません）"},
            },
        },
        read_only=False,
        handler=issue_provisioning_token,
    ),
    AgentTool(
        name="tenant_provisioning_token_list",
        description="自テナントの発行済みプロビジョニングトークン一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_my_provisioning_tokens,
    ),
]
