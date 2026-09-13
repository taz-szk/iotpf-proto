from app.services.rag_tools import AgentTool  # noqa: F401  (再エクスポートしないが型参照用)

from . import alerts, dashboard, provisioning  # noqa: E402

TENANT_TOOLS: list[AgentTool] = [
    *provisioning.TOOLS,
    *dashboard.TOOLS,
    *alerts.TOOLS,
]
# 注意: 全テナント一覧(tenant_list相当)は絶対に追加しないこと。
# テナント自身が他テナントの情報を取得できてしまう越境漏洩になる。
