"""本番のbilling_batchと全く同じ関数(_count_influxdb_retained_points)を、
テナント自身のinfluxdb_token(admin tokenではない)で直接呼び出し、
retained_data_pointsが0になる原因を調査するデバッグスクリプト。"""
import httpx
from app.database import SessionLocal
from app.config import settings
from app.models.public import Tenant
from app.services.billing import get_effective_retention_days
from app.services.billing_usage import _count_influxdb_retained_points

with SessionLocal() as db:
    tenants = db.query(Tenant).filter(
        Tenant.status == "active", Tenant.influxdb_org_id.isnot(None)
    ).all()
    tenant_infos = [
        (t.name, t.influxdb_org_id, t.influxdb_token, get_effective_retention_days(db, t))
        for t in tenants
    ]

for name, org_id, token, retention_days in tenant_infos:
    print(f"\n########## tenant={name} org_id={org_id} retention_days={retention_days} token_set={bool(token)} ##########")
    active_count = _count_influxdb_retained_points(org_id, token or "", retention_days, archived=False)
    archived_count = _count_influxdb_retained_points(org_id, token or "", retention_days, archived=True)
    print(f"retained_data_points(現役)={active_count}  retired_data_points(退役)={archived_count}")

    # 生のレスポンスも確認する(現役側)
    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: -{retention_days}d)\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> filter(fn: (r) => not (r.device_name =~ /^Del_/))\n'
        '  |> group()\n'
        '  |> count()\n'
        '  |> sum()\n'
    )
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/query?orgID={org_id}",
        headers={"Authorization": f"Token {token or ''}", "Content-Type": "application/json"},
        json={"query": query, "type": "flux"},
        timeout=15.0,
    )
    print(f"生レスポンス(現役, テナントtoken使用): status={resp.status_code} body={resp.text!r}")

    resp_admin = httpx.post(
        f"{settings.influxdb_url}/api/v2/query?orgID={org_id}",
        headers={"Authorization": f"Token {settings.influxdb_admin_token}", "Content-Type": "application/json"},
        json={"query": query, "type": "flux"},
        timeout=15.0,
    )
    print(f"生レスポンス(現役, admin token使用):   status={resp_admin.status_code} body={resp_admin.text!r}")
