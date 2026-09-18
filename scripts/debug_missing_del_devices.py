"""device_deletedには存在するがGrafanaのデバイス一覧に出てこないDel_接頭辞デバイスについて、
device_status(online)のデータが存在するか・いつのものかを確認するデバッグスクリプト。"""
import httpx
from app.database import SessionLocal
from app.config import settings
from app.services.billing import get_effective_retention_days
from app.models.public import Tenant

TARGET_DEVICE_NAMES = ["Del_dev01", "Del_dev02", "Del_sim-001"]

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
    device_list = "|".join(TARGET_DEVICE_NAMES)
    status, csv_check = run_flux(org_id, f'''
from(bucket: "telemetry")
  |> range(start: -{retention_days}d)
  |> filter(fn: (r) => r._measurement == "device_status")
  |> filter(fn: (r) => r._field == "online")
  |> filter(fn: (r) => r.device_name =~ /^({device_list})$/)
  |> sort(columns: ["_time"])
''')
    if "_result" not in csv_check:
        continue
    print(f"\n########## tenant={name} org_id={org_id} retention_days={retention_days} ##########")
    print(f"=== 対象device_nameのdevice_status(online)全履歴 (status={status}) ===")
    print(csv_check)

    _, csv_group = run_flux(org_id, f'''
from(bucket: "telemetry")
  |> range(start: -{retention_days}d)
  |> filter(fn: (r) => r._measurement == "device_status")
  |> filter(fn: (r) => r._field == "online")
  |> filter(fn: (r) => r.device_name =~ /^({device_list})$/)
  |> group(columns: ["device_name"])
  |> last()
''')
    print("=== group(device_name)+last()適用後(GrafanaのhasStatus判定と同じ条件、直近30d外でも見えるようrange拡大版) ===")
    print(csv_group)
