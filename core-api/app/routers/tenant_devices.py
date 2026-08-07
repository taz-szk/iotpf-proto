from uuid import UUID
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import text
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.services.auth import verify_token

router = APIRouter(prefix="/tenants/{tenant_id}/devices", tags=["tenant-devices"])
_bearer = HTTPBearer()


class DeviceOut(BaseModel):
    id: str
    device_id: str
    connection_status: str
    last_seen_at: Optional[str] = None
    fw_version: Optional[str] = None
    cert_not_after: Optional[str] = None
    created_at: str


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _get_active_tenant(tenant_id_str: str):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.id == tenant_id_str, Tenant.status == "active"
        ).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.get("", response_model=list[DeviceOut])
def list_tenant_devices(tenant_id: UUID, _: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'''
                SELECT id, device_id, connection_status, last_seen_at,
                       fw_version, cert_not_after, created_at
                FROM "{schema}".devices
                ORDER BY created_at DESC
                LIMIT 1000
            ''')
        ).fetchall()
    return [
        DeviceOut(
            id=str(r.id),
            device_id=r.device_id,
            connection_status=r.connection_status,
            last_seen_at=r.last_seen_at.isoformat() if r.last_seen_at else None,
            fw_version=r.fw_version,
            cert_not_after=r.cert_not_after.isoformat() if r.cert_not_after else None,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
