import os
import httpx
from sqlalchemy import text
from app.database import SessionLocal
from app.config import settings

DEVICE_NAME = os.environ.get("DEBUG_DEVICE_NAME", "dev-001")

with SessionLocal() as db:
    tenants = db.execute(text('SELECT id, name, influxdb_org_id FROM public.tenants')).fetchall()

target = None
for t in tenants:
    schema = f"tenant_{str(t.id).replace('-', '_')}"
    with SessionLocal() as db:
        row = db.execute(
            text(f'SELECT device_id, device_name FROM "{schema}".devices WHERE device_name = :dn'),
            {"dn": DEVICE_NAME},
        ).first()
    if row:
        target = (t, row)
        print(f"[devices table] tenant={t.name} org_id={t.influxdb_org_id} device_id={row.device_id} device_name={row.device_name}")
        break

if not target:
    print(f"!! {DEVICE_NAME} は devices テーブルに見つかりませんでした")
    raise SystemExit(1)

tenant, _ = target
org_id = tenant.influxdb_org_id


def run_flux(query, label):
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
    print(f"\n=== {label} (status={resp.status_code}) ===")
    print(resp.text)


run_flux(
    f'''
from(bucket: "telemetry")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "device_deleted")
  |> filter(fn: (r) => r.device_name == "{DEVICE_NAME}")
  |> sort(columns: ["_time"])
''',
    "device_deleted マーカー全履歴(時系列順)",
)

run_flux(
    f'''
from(bucket: "telemetry")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "device_status")
  |> filter(fn: (r) => r._field == "online")
  |> filter(fn: (r) => r.device_name == "{DEVICE_NAME}")
  |> group(columns: ["device_name"])
  |> last()
''',
    "device_status(online) 最新値",
)

run_flux(
    f'''
status = from(bucket: "telemetry")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "device_status")
  |> filter(fn: (r) => r._field == "online")
  |> group(columns: ["device_name"])
  |> last()
  |> map(fn: (r) => ({{device_name: r.device_name, src: "status"}}))

deleted = from(bucket: "telemetry")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "device_deleted")
  |> filter(fn: (r) => not (r.device_name =~ /^Del_/))
  |> group(columns: ["device_name"])
  |> last()
  |> filter(fn: (r) => r._value == 1)
  |> map(fn: (r) => ({{device_name: r.device_name, src: "deleted"}}))

union(tables: [status, deleted])
  |> group(columns: ["device_name"])
  |> reduce(
       fn: (r, accumulator) => ({{
         device_name: r.device_name,
         hasStatus: if r.src == "status" then true else accumulator.hasStatus,
         hasDeleted: if r.src == "deleted" then true else accumulator.hasDeleted,
       }}),
       identity: {{device_name: "", hasStatus: false, hasDeleted: false}},
     )
  |> filter(fn: (r) => r.device_name == "{DEVICE_NAME}")
''',
    "デバイス一覧変数と同じロジックでのdev-001の判定結果(hasStatus/hasDeleted)",
)
