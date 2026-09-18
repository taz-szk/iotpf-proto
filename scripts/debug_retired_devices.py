"""退役(Del_接頭辞)デバイスの一覧と、それぞれのdevice_deletedマーカーの最終更新時刻を
テナントごとに表示する。保持日数ウィンドウ内の件数と、Grafanaの一覧が使う直近30日
ウィンドウ内の件数を分けて出し、件数の食い違いの原因を特定するためのデバッグスクリプト。"""
import httpx
from app.database import SessionLocal
from app.config import settings
from app.services.billing import get_effective_retention_days
from app.models.public import Tenant

with SessionLocal() as db:
    tenants = db.query(Tenant).filter(
        Tenant.status == "active", Tenant.influxdb_org_id.isnot(None)
    ).all()
    tenant_infos = [(t.id, t.name, t.influxdb_org_id, get_effective_retention_days(db, t)) for t in tenants]


def run_flux(org_id, query):
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/query?orgID={org_id}",
        headers={
            "Authorization": f"Token {settings.influxdb_admin_token}",
            "Content-Type": "application/json",
            "Accept": "application/csv",
        },
        json={"query": query, "type": "flux"},
        timeout=30.0,
    )
    return resp.status_code, resp.text


for tenant_id, name, org_id, retention_days in tenant_infos:
    status, csv_all = run_flux(org_id, f'''
from(bucket: "telemetry")
  |> range(start: -{retention_days}d)
  |> filter(fn: (r) => r._measurement == "device_deleted")
  |> filter(fn: (r) => r.device_name =~ /^Del_/)
  |> group(columns: ["device_name"])
  |> last()
  |> sort(columns: ["_time"])
''')
    if "_result" not in csv_all:
        continue

    print(f"\n########## tenant={name} org_id={org_id} retention_days={retention_days} ##########")
    print(f"=== 保持日数({retention_days}日)ウィンドウ内のDel_デバイス一覧(最終マーカー) (status={status}) ===")
    print(csv_all)

    _, csv_30d = run_flux(org_id, '''
from(bucket: "telemetry")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "device_deleted")
  |> filter(fn: (r) => r.device_name =~ /^Del_/)
  |> group(columns: ["device_name"])
  |> last()
''')
    print("=== 直近30日ウィンドウ内のDel_デバイス一覧(Grafanaの一覧と同じ条件) ===")
    print(csv_30d)
