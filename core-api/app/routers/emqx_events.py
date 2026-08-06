from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.database import SessionLocal
from sqlalchemy import text
from datetime import datetime, timezone
import re

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

@router.post("/events")
def handle_device_event(req: DeviceEvent):
    tenant_id, device_id = _parse_clientid(req.clientid)
    if not tenant_id:
        return {"result": "ignored"}
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        return {"result": "ignored"}

    schema = f"tenant_{tenant_id.replace('-', '_')}"
    status = "online" if req.event in ("client.connected",) else "offline"
    now = datetime.now(timezone.utc)

    try:
        with SessionLocal() as db:
            db.execute(text(f'''
                UPDATE "{schema}".devices
                SET connection_status = :status, last_seen_at = :now
                WHERE device_id = :did
            '''), {"status": status, "now": now, "did": device_id})
            db.commit()
    except Exception:
        pass

    return {"result": "ok"}
