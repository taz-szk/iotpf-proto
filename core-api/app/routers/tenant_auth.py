from datetime import timedelta
from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from app.schemas.tenant_auth import TenantLoginRequest, TenantLoginResponse
import secrets as _secrets
from app.services.auth import verify_password, create_access_token, hash_password, verify_token
from app.services.rate_limiter import is_rate_limited, record_failure, clear_failures
from app.services.grafana import ensure_grafana_user_in_org, set_user_default_org_via_proxy
from app.models.public import Tenant, MfaSettings
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
def tenant_login(req: TenantLoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    rate_key = f"tenant_login:{ip}"
    if is_rate_limited(rate_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.slug == req.tenant_slug,
            Tenant.status == "active"
        ).first()
    if not tenant:
        _equalize_timing(req.password)  # timing equalization
        record_failure(rate_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if tenant.grafana_org_id is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Grafana provisioning incomplete for this tenant")

    schema = f"tenant_{str(tenant.id).replace('-', '_')}"
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT id, email, password_hash, role, totp_enabled FROM "{schema}".users WHERE email = :email AND is_active = TRUE'),
            {"email": req.email}
        ).fetchone()

    if not row:
        _equalize_timing(req.password)  # timing equalization
        record_failure(rate_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(req.password, row.password_hash):
        record_failure(rate_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    clear_failures(rate_key)

    # MFA 設定確認
    with SessionLocal() as db:
        mfa = db.query(MfaSettings).filter(MfaSettings.id == 1).first()
    mfa_required = mfa.tenant_required if mfa else False

    totp_enabled = bool(row.totp_enabled) if hasattr(row, "totp_enabled") else False

    if mfa_required:
        partial_payload = {
            "sub": str(row.id),
            "email": row.email,
            "type": "partial_tenant",
            "tenant_id": str(tenant.id),
            "role": row.role,
        }
        partial_token = create_access_token(partial_payload, expires_delta=timedelta(minutes=10))
        if totp_enabled:
            return TenantLoginResponse(status="totp_required", partial_token=partial_token)
        else:
            return TenantLoginResponse(status="totp_setup_required", partial_token=partial_token)

    # Grafana org にユーザーを同期し、デフォルト org を設定する
    # 初回ログイン時に org メンバーシップが作られていない場合の保険
    if tenant.grafana_org_id:
        try:
            g_org_id = int(tenant.grafana_org_id)
            g_login = f"{str(tenant.id)}:{req.email}"
            ensure_grafana_user_in_org(g_org_id, req.email, "Viewer", str(tenant.id))
            set_user_default_org_via_proxy(g_login, g_org_id)
        except Exception as e:
            print(f"[tenant_login] grafana sync warning (non-fatal): {e}")

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
        status="ok",
        user_id=str(row.id),
        email=row.email,
        role=row.role,
        tenant_id=str(tenant.id),
        redirect_url="/admin/tenant-portal.html",
    )


@router.post("/logout")
def tenant_logout(response: Response):
    response.delete_cookie(key="iot_token", path="/")
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.get("/me")
def get_me(iot_token: str = Cookie(default=None)):
    if not iot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(iot_token)
    if not payload or payload.get("type") != "tenant":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    return {
        "user_id": payload["sub"],
        "email": payload["email"],
        "role": payload["role"],
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "grafana_org_id": tenant.grafana_org_id,
    }


@router.get("/me/devices")
def get_my_devices(iot_token: str = Cookie(default=None)):
    if not iot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(iot_token)
    if not payload or payload.get("type") != "tenant":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    tenant_id = payload["tenant_id"]
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT id, device_id, device_name, connection_status, last_seen FROM "{schema}".devices ORDER BY device_id')
        ).fetchall()

    return [
        {
            "id": str(r.id),
            "device_id": r.device_id,
            "device_name": r.device_name or r.device_id,
            "connection_status": r.connection_status,
            "last_seen": str(r.last_seen) if r.last_seen else None,
        }
        for r in rows
    ]


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(req: ChangePasswordRequest, iot_token: str = Cookie(default=None)):
    if not iot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(iot_token)
    if not payload or payload.get("type") != "tenant":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]
    schema = f"tenant_{tenant_id.replace('-', '_')}"

    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT password_hash FROM "{schema}".users WHERE id = :uid AND is_active = TRUE'),
            {"uid": user_id},
        ).fetchone()

    if not row or not verify_password(req.current_password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    with engine.connect() as conn:
        conn.execute(
            text(f'UPDATE "{schema}".users SET password_hash = :hash WHERE id = :uid'),
            {"hash": hash_password(req.new_password), "uid": user_id},
        )
        conn.commit()
