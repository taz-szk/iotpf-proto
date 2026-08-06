from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.schemas.tenant import TenantCreate, TenantOut
from app.models.public import Tenant
from app.database import SessionLocal
from app.services.auth import verify_token
from app.services.tenant import setup_tenant
import uuid

router = APIRouter(prefix="/tenants")
_bearer = HTTPBearer()

def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload

@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreate, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        existing = db.query(Tenant).filter(Tenant.slug == body.slug).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already exists")
        tenant = Tenant(id=uuid.uuid4(), name=body.name, slug=body.slug)
        db.add(tenant)
        db.flush()
        tenant_id = str(tenant.id)
        org_id, token, grafana_org_id = setup_tenant(tenant_id, body.name)
        tenant.influxdb_org_id = org_id
        tenant.influxdb_token = token
        tenant.grafana_org_id = str(grafana_org_id)
        db.commit()
        db.refresh(tenant)
        return tenant

@router.get("", response_model=list[TenantOut])
def list_tenants(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        return db.query(Tenant).filter(Tenant.status == "active").all()

@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: str, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        return tenant
