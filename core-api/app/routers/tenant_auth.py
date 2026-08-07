from datetime import timedelta
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import text
from app.schemas.tenant_auth import TenantLoginRequest, TenantLoginResponse
from app.services.auth import verify_password, create_access_token
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.config import settings

router = APIRouter(prefix="/tenant-auth", tags=["tenant-auth"])


@router.post("/login", response_model=TenantLoginResponse)
def tenant_login(req: TenantLoginRequest, response: Response):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.slug == req.tenant_slug,
            Tenant.status == "active"
        ).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    schema = f"tenant_{str(tenant.id).replace('-', '_')}"
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT id, email, password_hash, role FROM "{schema}".users WHERE email = :email AND is_active = TRUE'),
            {"email": req.email}
        ).fetchone()

    if not row or not verify_password(req.password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    payload = {
        "sub": str(row.id),
        "email": row.email,
        "type": "tenant",
        "tenant_id": str(tenant.id),
        "role": row.role,
    }
    token = create_access_token(
        payload,
        expires_delta=timedelta(hours=settings.grafana_session_expire_hours),
    )
    expire_seconds = settings.grafana_session_expire_hours * 3600
    response.set_cookie(
        key="iot_token", value=token,
        httponly=True, secure=True, samesite="lax",
        max_age=expire_seconds, path="/",
    )
    return TenantLoginResponse(
        user_id=str(row.id),
        email=row.email,
        role=row.role,
        tenant_id=str(tenant.id),
        redirect_url=f"/grafana/?orgId={tenant.grafana_org_id}",
    )


@router.post("/logout")
def tenant_logout(response: Response):
    response.delete_cookie(key="iot_token", path="/")
    return {"ok": True}
