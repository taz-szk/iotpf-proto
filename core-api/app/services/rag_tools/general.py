from app.database import SessionLocal
from app.models.public import Tenant
from app.routers.billing import get_tenant_invoice
from app.routers.stats import get_tenant_stats
from app.services.rag_tools import AgentTool

_SYSTEM_PAYLOAD = {"sub": "00000000-0000-0000-0000-000000000000", "email": "assistant@platform", "type": "platform"}


def list_all_tenants() -> list[dict]:
    with SessionLocal() as db:
        rows = db.query(Tenant).filter(Tenant.status != "deleted").order_by(Tenant.name).all()
        return [{"id": str(t.id), "name": t.name} for t in rows]


def get_tenant_stats_tool(tenant_id: str) -> dict:
    return get_tenant_stats(tenant_id=tenant_id, _=_SYSTEM_PAYLOAD)


def get_tenant_invoice_tool(tenant_id: str, target_year_month: str) -> dict:
    return get_tenant_invoice(tenant_id=tenant_id, target_year_month=target_year_month, _=_SYSTEM_PAYLOAD)


TOOLS = [
    AgentTool(
        name="tenant_list",
        description="全テナントの名前とIDの一覧を取得する。質問文に出てくるテナント名をIDに変換するために使う。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=lambda tenant_id=None: list_all_tenants(),
    ),
    AgentTool(
        name="tenant_stats_get",
        description="テナントの統計情報（デバイス数・データポイント数・アラート件数等）を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=get_tenant_stats_tool,
    ),
    AgentTool(
        name="tenant_invoice_get",
        description="テナントの指定月の請求書明細を取得する。",
        input_schema={
            "type": "object",
            "properties": {"target_year_month": {"type": "string", "description": "対象年月（YYYY-MM形式）"}},
            "required": ["target_year_month"],
        },
        read_only=True,
        handler=get_tenant_invoice_tool,
    ),
]
