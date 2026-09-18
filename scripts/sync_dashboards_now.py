"""既存Grafanaダッシュボードのtemplating/panelsを最新のコード定義で再同期する。
POST /platform/sync-dashboards エンドポイントと同じ処理を、認証トークン無しで
core-apiコンテナ内から直接実行するためのスクリプト。
device_name変数のFluxクエリ修正など、既にGrafana側に保存済みのダッシュボードJSONへ
コード側の変更を反映させたい場合に使う。"""
from sqlalchemy import text
from app.database import SessionLocal
from app.models.public import Tenant
from app.services.device_groups import list_groups_with_devices
from app.services.grafana import (
    sync_tenant_dashboard, sync_platform_dashboard, get_or_create_platform_org,
)

with SessionLocal() as db:
    tenants = db.query(Tenant).filter(
        Tenant.status == "active", Tenant.grafana_org_id.isnot(None)
    ).all()
    tenants = [(t.id, t.name, t.grafana_org_id) for t in tenants]

for tenant_id, name, grafana_org_id in tenants:
    schema = f"tenant_{str(tenant_id).replace('-', '_')}"
    try:
        groups = list_groups_with_devices(schema)
        sync_tenant_dashboard(grafana_org_id, name, groups)
        print(f"OK: {name}")
    except Exception as e:
        print(f"ERROR: {name}: {e}")

try:
    platform_org_id = get_or_create_platform_org()
    sync_platform_dashboard(platform_org_id)
    print("OK: platform-admin")
except Exception as e:
    print(f"ERROR: platform-admin: {e}")
