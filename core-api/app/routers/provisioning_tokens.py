from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from app.models.public import ProvisioningToken, Tenant
from app.database import SessionLocal
from app.services.auth import verify_token
import uuid, secrets

router = APIRouter()
_bearer = HTTPBearer()

def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload

class TokenCreate(BaseModel):
    max_devices: int = 100
    expires_days: int = 365

class TokenOut(BaseModel):
    id: str
    token: str
    tenant_id: str
    max_devices: int
    registered_count: int
    expires_at: datetime
    is_active: bool

    class Config:
        from_attributes = True

@router.post("/tenants/{tenant_id}/provisioning-tokens", response_model=TokenOut, status_code=201)
def create_provisioning_token(tenant_id: str, body: TokenCreate, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        token = ProvisioningToken(
            id=uuid.uuid4(),
            token=secrets.token_urlsafe(32),
            tenant_id=uuid.UUID(tenant_id),
            max_devices=body.max_devices,
            expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_days),
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        return TokenOut(
            id=str(token.id),
            token=token.token,
            tenant_id=str(token.tenant_id),
            max_devices=token.max_devices,
            registered_count=token.registered_count,
            expires_at=token.expires_at,
            is_active=token.is_active,
        )

@router.get("/tenants/{tenant_id}/provisioning-tokens", response_model=list[TokenOut])
def list_provisioning_tokens(tenant_id: str, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tokens = db.query(ProvisioningToken).filter(
            ProvisioningToken.tenant_id == tenant_id,
            ProvisioningToken.is_active == True,
        ).all()
        return [TokenOut(
            id=str(t.id), token=t.token, tenant_id=str(t.tenant_id),
            max_devices=t.max_devices, registered_count=t.registered_count,
            expires_at=t.expires_at, is_active=t.is_active,
        ) for t in tokens]

@router.delete("/tenants/{tenant_id}/provisioning-tokens/{token_id}", status_code=204)
def revoke_provisioning_token(tenant_id: str, token_id: str, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        token = db.query(ProvisioningToken).filter(
            ProvisioningToken.id == token_id,
            ProvisioningToken.tenant_id == tenant_id,
        ).first()
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        token.is_active = False
        db.commit()
