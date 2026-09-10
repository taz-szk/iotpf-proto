import re
from uuid import UUID
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import text
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.services.auth import verify_token
from app.services.grafana import retire_device_in_influxdb
from app.services.audit import log_audit
from app.services.device_groups import DeviceNotFoundError, GroupNotFoundError, assign_device_group, resync_grafana_groups

router = APIRouter(prefix="/tenants/{tenant_id}/devices", tags=["tenant-devices"])
_bearer = HTTPBearer()


def _validate_uuid(value: str, field: str = "id") -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid {field}")
    return value.lower()


class DeviceOut(BaseModel):
    id: str
    device_id: str
    device_name: Optional[str] = None
    connection_status: str
    last_seen_at: Optional[str] = None
    fw_version: Optional[str] = None
    cert_not_after: Optional[str] = None
    created_at: str
    group_id: Optional[str] = None


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


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant_device(tenant_id: UUID, device_id: str, payload: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    tenant = _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT device_name FROM "{schema}".devices WHERE device_id = :did'),
            {"did": device_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        device_name = row.device_name or device_id
        conn.execute(text(f'DELETE FROM "{schema}".devices WHERE device_id = :did'), {"did": device_id})
        conn.commit()
    log_audit("platform", payload["sub"], payload["email"], "delete_device",
              tenant_id=tenant_id_str, resource_type="device", resource_id=device_id)
    if tenant.influxdb_org_id:
        retire_device_in_influxdb(tenant.influxdb_org_id, device_name)


@router.get("", response_model=list[DeviceOut])
def list_tenant_devices(tenant_id: UUID, _: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'''
                SELECT id, device_id, device_name, connection_status, last_seen_at,
                       fw_version, cert_not_after, created_at, group_id
                FROM "{schema}".devices
                ORDER BY created_at DESC
                LIMIT 1000
            ''')
        ).fetchall()
    return [
        DeviceOut(
            id=str(r.id),
            device_id=r.device_id,
            device_name=r.device_name,
            connection_status=r.connection_status,
            last_seen_at=r.last_seen_at.isoformat() if r.last_seen_at else None,
            fw_version=r.fw_version,
            cert_not_after=r.cert_not_after.isoformat() if r.cert_not_after else None,
            created_at=r.created_at.isoformat(),
            group_id=str(r.group_id) if r.group_id else None,
        )
        for r in rows
    ]


class DeviceUpdateBody(BaseModel):
    group_id: Optional[str] = None


@router.patch("/{device_id}", response_model=DeviceOut)
def update_tenant_device(tenant_id: UUID, device_id: str, body: DeviceUpdateBody, payload: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    if body.group_id is not None:
        body.group_id = _validate_uuid(body.group_id, "group_id")
    try:
        old_group_id = assign_device_group(schema, device_id, body.group_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    log_audit("platform", payload["sub"], payload["email"], "assign_device_group",
              tenant_id=tenant_id_str, resource_type="device", resource_id=device_id,
              detail={"old_group_id": old_group_id, "new_group_id": body.group_id})
    resync_grafana_groups(tenant_id_str, schema)
    with engine.connect() as conn:
        row = conn.execute(text(f'''
            SELECT id, device_id, device_name, connection_status, last_seen_at,
                   fw_version, cert_not_after, created_at, group_id
            FROM "{schema}".devices WHERE device_id = :did
        '''), {"did": device_id}).fetchone()
    return DeviceOut(
        id=str(row.id), device_id=row.device_id, device_name=row.device_name,
        connection_status=row.connection_status,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
        fw_version=row.fw_version,
        cert_not_after=row.cert_not_after.isoformat() if row.cert_not_after else None,
        created_at=row.created_at.isoformat(),
        group_id=str(row.group_id) if row.group_id else None,
    )
