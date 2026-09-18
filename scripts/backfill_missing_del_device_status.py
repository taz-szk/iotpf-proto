"""device_deletedマーカーは存在するがdevice_status(online)データが無いDel_接頭辞デバイスに、
不足しているdevice_status,online=0iマーカーを一度だけ書き込むバックフィルスクリプト。
バグ修正前の古いretire_device_in_influxdbで削除された古いアーカイブがGrafanaの
デバイス一覧に出てこない問題を解消するため。実行しても既に揃っているデバイスには
何も書き込まない(冪等)。"""
import time
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


def parse_values(csv_text: str) -> set[str]:
    """`_value`列を持つCSV応答からその列の値集合を取り出す。"""
    lines = [l for l in csv_text.splitlines() if l.startswith(",_result,")]
    if not lines:
        return set()
    header_line = next((l for l in csv_text.splitlines() if l.startswith(",result,")), None)
    if not header_line:
        return set()
    cols = header_line.split(",")
    try:
        value_idx = cols.index("_value")
    except ValueError:
        return set()
    values = set()
    for line in lines:
        parts = line.split(",")
        if len(parts) > value_idx:
            values.add(parts[value_idx])
    return values


for tenant_id, name, org_id, retention_days in tenant_infos:
    status_del, csv_deleted = run_flux(org_id, f'''
from(bucket: "telemetry")
  |> range(start: -{retention_days}d)
  |> filter(fn: (r) => r._measurement == "device_deleted")
  |> filter(fn: (r) => r.device_name =~ /^Del_/)
  |> keep(columns: ["device_name"])
  |> group()
  |> distinct(column: "device_name")
''')
    if status_del != 200:
        print(f"skip tenant={name}: device_deleted query failed ({status_del}) {csv_deleted[:200]}")
        continue
    deleted_names = parse_values(csv_deleted)

    status_st, csv_status = run_flux(org_id, f'''
from(bucket: "telemetry")
  |> range(start: -{retention_days}d)
  |> filter(fn: (r) => r._measurement == "device_status")
  |> filter(fn: (r) => r._field == "online")
  |> filter(fn: (r) => r.device_name =~ /^Del_/)
  |> keep(columns: ["device_name"])
  |> group()
  |> distinct(column: "device_name")
''')
    if status_st != 200:
        print(f"skip tenant={name}: device_status query failed ({status_st}) {csv_status[:200]}")
        continue
    status_names = parse_values(csv_status)

    missing = sorted(deleted_names - status_names)
    if not missing:
        print(f"tenant={name}: 不足なし({len(deleted_names)}件とも揃っています)")
        continue

    print(f"tenant={name}: device_statusが不足しているDel_デバイス: {missing}")
    now_ns = int(time.time()) * 1_000_000_000
    lines = []
    for device_name in missing:
        esc = device_name.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")
        lines.append(f"device_status,device_name={esc} online=0i {now_ns}")
    payload = "\n".join(lines)
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/write"
        f"?orgID={org_id}&bucket=telemetry&precision=ns",
        headers={
            "Authorization": f"Token {settings.influxdb_admin_token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        content=payload.encode(),
        timeout=10.0,
    )
    print(f"  -> write status={resp.status_code} body={resp.text[:200]}")
