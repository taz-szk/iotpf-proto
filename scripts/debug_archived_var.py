"""新設したarchived_device_name変数のFluxクエリ(_FLUX_DEVICE_VAR_ARCHIVED)を
実際にInfluxDBへ投げて、返ってくるdevice_name一覧を直接確認するデバッグスクリプト。"""
import httpx
from app.database import SessionLocal
from app.config import settings
from app.models.public import Tenant
from app.services.grafana import _FLUX_DEVICE_VAR_ARCHIVED

with SessionLocal() as db:
    tenants = db.query(Tenant).filter(
        Tenant.status == "active", Tenant.influxdb_org_id.isnot(None)
    ).all()
    tenant_infos = [(t.name, t.influxdb_org_id) for t in tenants]

for name, org_id in tenant_infos:
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/query?orgID={org_id}",
        headers={
            "Authorization": f"Token {settings.influxdb_admin_token}",
            "Content-Type": "application/json",
            "Accept": "application/csv",
        },
        json={"query": _FLUX_DEVICE_VAR_ARCHIVED, "type": "flux"},
        timeout=30.0,
    )
    print(f"\n########## tenant={name} org_id={org_id} (status={resp.status_code}) ##########")
    print(repr(resp.text))
