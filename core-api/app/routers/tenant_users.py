import uuid
from uuid import UUID
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.schemas.tenant_users import TenantUserCreate, TenantUserOut
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.services.auth import verify_token, hash_password
from app.services.grafana import ensure_grafana_user_in_org

router = APIRouter(prefix="/tenants/{tenant_id}/users", tags=["tenant-users"])
_bearer = HTTPBearer()

_ROLE_GRAFANA = {"admin": "Admin", "operator": "Editor", "viewer": "Viewer"}

def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload

def _get_active_tenant(tenant_id: str):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id, Tenant.status == "active").first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant

@router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
def create_tenant_user(tenant_id: UUID, body: TenantUserCreate, _: dict = Depends(_require_platform)):
    if body.role not in _ROLE_GRAFANA:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(_ROLE_GRAFANA)}")
    tenant_id_str = str(tenant_id)
    tenant = _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    user_id = str(uuid.uuid4())
    password_hash = hash_password(body.password)

    with engine.connect() as conn:
        try:
            conn.execute(
                text(f'''
                    INSERT INTO "{schema}".users (id, email, password_hash, role)
                    VALUES (:id, :email, :hash, :role)
                '''),
                {"id": user_id, "email": body.email, "hash": password_hash, "role": body.role}
            )
            if tenant.grafana_org_id:
                ensure_grafana_user_in_org(int(tenant.grafana_org_id), body.email, _ROLE_GRAFANA[body.role], str(tenant_id))
            conn.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Email already exists")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=502, detail="Grafana sync failed; user not created")

    return TenantUserOut(
        id=user_id, email=body.email, role=body.role,
        is_active=True, created_at="",
    )

@router.get("", response_model=list[TenantUserOut])
def list_tenant_users(tenant_id: UUID, _: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    tenant = _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT id, email, role, is_active, created_at FROM "{schema}".users ORDER BY created_at DESC')
        ).fetchall()
    return [
        TenantUserOut(
            id=str(r.id), email=r.email, role=r.role,
            is_active=r.is_active, created_at=str(r.created_at),
        )
        for r in rows
    ]
