from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.database import SessionLocal
from app.models.public import Tenant
from app.config import settings
from sqlalchemy import text
from datetime import datetime, timezone
import re
import httpx

router = APIRouter(prefix="/emqx")

class DeviceEvent(BaseModel):
    clientid: str = ""
    event: str = ""
    username: str = ""
    peerhost: str = ""
    timestamp: Optional[int] = None

def _parse_clientid(clientid: str):
    m = re.fullmatch(r'([^:]+):([^:]+)', clientid)
    if not m:
        return None, None
    return m.group(1), m.group(2)

def _write_status_to_influxdb(org_id: str, tenant_id: str, device_id: str, device_name: str, online: int) -> None:
    esc = device_name.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")
    line = f'device_status,tenant_id={tenant_id},device_id={device_id},device_name={esc} online={online}i'
    try:
        httpx.post(
            f"{settings.influxdb_url}/api/v2/write?orgID={org_id}&bucket=telemetry&precision=ns",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            content=line.encode(),
            timeout=3.0,
        )
    except Exception:
        pass


@router.post("/events")
def handle_device_event(req: DeviceEvent):
    tenant_id, device_id = _parse_clientid(req.clientid)
    if not tenant_id:
        return {"result": "ignored"}
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        return {"result": "ignored"}

    schema = f"tenant_{tenant_id.replace('-', '_')}"
    conn_status = "online" if req.event in ("client.connected",) else "offline"
    online_val = 1 if conn_status == "online" else 0
    now = datetime.now(timezone.utc)

    device_name = device_id
    influxdb_org_id = None
    try:
        with SessionLocal() as db:
            row = db.execute(text(f'''
                SELECT device_name FROM "{schema}".devices WHERE device_id = :did
            '''), {"did": device_id}).fetchone()
            if row and row.device_name:
                device_name = row.device_name
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                influxdb_org_id = tenant.influxdb_org_id
            db.execute(text(f'''
                UPDATE "{schema}".devices
                SET connection_status = :status, last_seen_at = :now
                WHERE device_id = :did
            '''), {"status": conn_status, "now": now, "did": device_id})
            db.commit()
    except Exception:
        pass

    if influxdb_org_id:
        _write_status_to_influxdb(influxdb_org_id, tenant_id, device_id, device_name, online_val)

    return {"result": "ok"}
