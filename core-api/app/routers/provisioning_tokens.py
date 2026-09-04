from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import text
from app.models.public import ProvisioningToken, Tenant
from app.database import SessionLocal, engine
from app.services.auth import verify_token
import uuid, secrets

_UNLIMITED_EXPIRES = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
_UNLIMITED_DEVICES = 2_000_000_000

router = APIRouter(prefix="/tenants")
_bearer = HTTPBearer()

def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload

def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}")

class TokenCreate(BaseModel):
    max_devices: Optional[int] = Field(default=100, gt=0, le=10000)
    expires_days: Optional[int] = Field(default=365, gt=0, le=1825)

class TokenOut(BaseModel):
    id: str
    token: str
    tenant_id: str
    max_devices: Optional[int]
    registered_count: int
    active_count: int = 0
    deleted_count: int = 0
    expires_at: Optional[datetime]
    is_active: bool

@router.post("/{tenant_id}/provisioning-tokens", response_model=TokenOut, status_code=201)
def create_provisioning_token(tenant_id: str, body: TokenCreate, _: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        token = ProvisioningToken(
            id=uuid.uuid4(),
            token=secrets.token_urlsafe(32),
            tenant_id=tenant_uuid,
            max_devices=_UNLIMITED_DEVICES if body.max_devices is None else body.max_devices,
            expires_at=_UNLIMITED_EXPIRES if body.expires_days is None
                       else datetime.now(timezone.utc) + timedelta(days=body.expires_days),
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        return TokenOut(
            id=str(token.id),
            token=token.token,
            tenant_id=str(token.tenant_id),
            max_devices=None if token.max_devices >= _UNLIMITED_DEVICES else token.max_devices,
            registered_count=token.registered_count,
            expires_at=None if token.expires_at >= _UNLIMITED_EXPIRES else token.expires_at,
            is_active=token.is_active,
        )

@router.get("/{tenant_id}/provisioning-tokens", response_model=list[TokenOut])
def list_provisioning_tokens(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    schema = f"tenant_{str(tenant_uuid).replace('-', '_')}"
    with SessionLocal() as db:
        tokens = db.query(ProvisioningToken).filter(
            ProvisioningToken.tenant_id == tenant_uuid,
            ProvisioningToken.is_active == True,
        ).all()
        result = []
        for t in tokens:
            try:
                active_count = db.execute(text(f'''
                    SELECT COUNT(*) FROM "{schema}".devices
                    WHERE provisioning_token_id = :tid
                '''), {"tid": str(t.id)}).scalar() or 0
            except Exception:
                active_count = 0
            result.append(TokenOut(
                id=str(t.id), token=t.token, tenant_id=str(t.tenant_id),
                max_devices=None if t.max_devices >= _UNLIMITED_DEVICES else t.max_devices,
                registered_count=t.registered_count,
                active_count=int(active_count),
                deleted_count=max(0, t.registered_count - int(active_count)),
                expires_at=None if t.expires_at >= _UNLIMITED_EXPIRES else t.expires_at,
                is_active=t.is_active,
            ))
        return result

@router.get("/{tenant_id}/provisioning-tokens/{token_id}/devices")
def list_token_devices(tenant_id: str, token_id: str, _: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    token_uuid = _parse_uuid(token_id, "token_id")
    schema = f"tenant_{str(tenant_uuid).replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(text(f'''
            SELECT device_id, device_name, connection_status, created_at
            FROM "{schema}".devices
            WHERE provisioning_token_id = :tid
            ORDER BY created_at DESC
        '''), {"tid": str(token_uuid)}).fetchall()
    return [
        {
            "device_id": r.device_id,
            "device_name": r.device_name or r.device_id,
            "connection_status": r.connection_status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

@router.delete("/{tenant_id}/provisioning-tokens/{token_id}", status_code=204)
def revoke_provisioning_token(tenant_id: str, token_id: str, _: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    token_uuid = _parse_uuid(token_id, "token_id")
    with SessionLocal() as db:
        token = db.query(ProvisioningToken).filter(
            ProvisioningToken.id == token_uuid,
            ProvisioningToken.tenant_id == tenant_uuid,
        ).first()
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        token.is_active = False
        db.commit()
    return
