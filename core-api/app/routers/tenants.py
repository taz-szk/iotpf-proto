from fastapi import APIRouter, BackgroundTasks, HTTPException, status, Depends, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.schemas.tenant import TenantCreate, TenantOut
from app.models.public import Tenant
from app.database import SessionLocal
from app.services.auth import verify_token
from app.services.tenant import setup_tenant, teardown_tenant
from app.services.grafana import get_or_create_platform_org, sync_all_tenants_to_platform_org, ensure_platform_admin_in_grafana, add_user_to_grafana_org, set_user_default_org_via_proxy
import time
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
        return db.query(Tenant).order_by(Tenant.name).all()

@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: str, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        return tenant


class TenantUpdate(BaseModel):
    name: str


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(tenant_id: str, body: TenantUpdate, _: dict = Depends(_require_platform)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant.name = body.name.strip()
        db.commit()
        db.refresh(tenant)
        return tenant


class TenantStatusUpdate(BaseModel):
    status: str


@router.delete("/{tenant_id}", status_code=status.HTTP_202_ACCEPTED)
def delete_tenant(tenant_id: str, background_tasks: BackgroundTasks, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if tenant.status == "deleted":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already being deleted")
        influxdb_org_id = tenant.influxdb_org_id
        grafana_org_id = tenant.grafana_org_id
        tenant.status = "deleted"
        tenant.slug = f"{tenant.slug}_del{int(time.time())}"
        db.commit()
    background_tasks.add_task(teardown_tenant, tenant_id, influxdb_org_id, grafana_org_id)
    return {"status": "deletion_queued"}


@router.patch("/{tenant_id}/status", response_model=TenantOut)
def update_tenant_status(tenant_id: str, body: TenantStatusUpdate, _: dict = Depends(_require_platform)):
    if body.status not in ("active", "suspended"):
        raise HTTPException(status_code=400, detail="status must be 'active' or 'suspended'")
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant.status = body.status
        db.commit()
        db.refresh(tenant)
        return tenant


@router.get("/platform/grafana-org-id")
def get_platform_grafana_org_id(payload: dict = Depends(_require_platform)):
    """管理者向け: プラットフォーム管理 Grafana org の ID を返す。
    呼び出すたびに全テナントのデータソースを platform-admin org に同期する。
    アクセスした管理者を platform org のメンバーとして追加する。
    """
    try:
        org_id = get_or_create_platform_org()
        # アクセスした管理者を platform org に追加（Grafana datasource へのアクセス権確保）
        email = payload.get("email", "")
        if email:
            try:
                ensure_platform_admin_in_grafana(email)
                add_user_to_grafana_org(org_id, email, "Admin")
                # platform-admin org をデフォルト org に設定（Auth Proxy 経由）
                set_user_default_org_via_proxy(email, org_id)
            except Exception:
                pass
        # 全テナント（deleted 以外）のデータソースを同期
        with SessionLocal() as db:
            rows = db.query(Tenant).filter(Tenant.status != "deleted").all()
            tenant_list = [
                {"name": t.name, "influxdb_org_id": t.influxdb_org_id, "tenant_id": str(t.id)}
                for t in rows if t.influxdb_org_id
            ]
        sync_all_tenants_to_platform_org(org_id, tenant_list)
        return {"grafana_org_id": org_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Grafana platform org error: {e}")
