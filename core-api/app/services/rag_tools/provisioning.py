from app.routers.provisioning_tokens import (
    TokenCreate,
    create_provisioning_token,
    list_provisioning_tokens,
)
from app.services.rag_tools import AgentTool

_SYSTEM_PAYLOAD = {"sub": "00000000-0000-0000-0000-000000000000", "email": "assistant@platform", "type": "platform"}


def issue_provisioning_token(tenant_id: str, max_devices: int | None = None, expires_days: int | None = None) -> dict:
    token = create_provisioning_token(
        tenant_id=tenant_id,
        body=TokenCreate(max_devices=max_devices, expires_days=expires_days),
        payload=_SYSTEM_PAYLOAD,
    )
    return token.model_dump() if hasattr(token, "model_dump") else dict(token)


def list_tenant_provisioning_tokens(tenant_id: str) -> list[dict]:
    tokens = list_provisioning_tokens(tenant_id=tenant_id, _=_SYSTEM_PAYLOAD)
    return [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in tokens]


TOOLS = [
    AgentTool(
        name="provisioning_token_issue",
        description="テナントの新しいプロビジョニングトークンを発行する。デバイスをプラットフォームに登録するために必要。",
        input_schema={
            "type": "object",
            "properties": {
                "max_devices": {"type": "integer", "description": "登録可能な最大デバイス数（省略時は100）"},
                "expires_days": {"type": "integer", "description": "有効期限（日数、省略時は365日）"},
            },
        },
        read_only=False,
        handler=issue_provisioning_token,
    ),
    AgentTool(
        name="provisioning_token_list",
        description="テナントの発行済みプロビジョニングトークン一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_tenant_provisioning_tokens,
    ),
]
