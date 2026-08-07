from datetime import timedelta
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import text
from app.schemas.tenant_auth import TenantLoginRequest, TenantLoginResponse
import secrets as _secrets
from app.services.auth import verify_password, create_access_token, hash_password
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.config import settings

router = APIRouter(prefix="/tenant-auth", tags=["tenant-auth"])

# Computed once at startup — ensures timing is always ~bcrypt-cost regardless of early exit.
# Falls back to a fixed, never-matching digest if the local bcrypt backend is unusable
# (e.g. passlib 1.7.4 + bcrypt 5.x in dev) so that importing this module never fails.
try:
    _DUMMY_HASH: str = hash_password(_secrets.token_hex(16))
except Exception:  # pragma: no cover - depends on local bcrypt backend
    _DUMMY_HASH = "$2b$12$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0"


def _equalize_timing(password: str) -> None:
    """早期リターン経路でも bcrypt 相当の時間を消費させ、テナント/メール列挙を防ぐ。"""
    try:
        verify_password(password, _DUMMY_HASH)
    except Exception:  # pragma: no cover - 戻り値は使わないため失敗しても無視
        pass


@router.post("/login", response_model=TenantLoginResponse)
def tenant_login(req: TenantLoginRequest, response: Response):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.slug == req.tenant_slug,
            Tenant.status == "active"
        ).first()
    if not tenant:
        _equalize_timing(req.password)  # timing equalization
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if tenant.grafana_org_id is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Grafana provisioning incomplete for this tenant")

    schema = f"tenant_{str(tenant.id).replace('-', '_')}"
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT id, email, password_hash, role FROM "{schema}".users WHERE email = :email AND is_active = TRUE'),
            {"email": req.email}
        ).fetchone()

    if not row:
        _equalize_timing(req.password)  # timing equalization
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(req.password, row.password_hash):
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
