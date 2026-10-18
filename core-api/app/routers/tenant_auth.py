import uuid as _audit_uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from app.schemas.tenant_auth import TenantLoginRequest, TenantLoginResponse
import secrets as _secrets
from app.services.audit import log_audit
from app.services.password_policy import validate_password
from app.services.auth import verify_password, create_access_token, hash_password, verify_token
from app.services.tenant_session import require_tenant_session, forget_session, password_fingerprint
from app.services.rate_limiter import is_rate_limited, record_failure, clear_failures
from app.services.grafana import ensure_grafana_user_in_org, set_user_default_org_via_proxy
from app.services.recaptcha import verify_recaptcha
from app.services.mailer import send_password_reset_email
from app.models.public import Tenant, MfaSettings, PasswordResetToken
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
        log_audit("tenant", str(_audit_uuid.UUID(int=0)), req.email, "login_failure",
                  result="failure", ip_address=ip)
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
        log_audit("tenant", str(_audit_uuid.UUID(int=0)), req.email, "login_failure",
                  tenant_id=str(tenant.id), result="failure", ip_address=ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(req.password, row.password_hash):
        record_failure(rate_key)
        log_audit("tenant", str(row.id), row.email, "login_failure",
                  tenant_id=str(tenant.id), result="failure", ip_address=ip)
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
        "pwv": password_fingerprint(row.password_hash),
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
    log_audit("tenant", str(row.id), row.email, "login_success",
              tenant_id=str(tenant.id), ip_address=ip)
    return TenantLoginResponse(
        status="ok",
        user_id=str(row.id),
        email=row.email,
        role=row.role,
        tenant_id=str(tenant.id),
        redirect_url="/admin/tenant-portal.html",
    )


@router.post("/logout")
def tenant_logout(response: Response, iot_token: str | None = Cookie(default=None)):
    if iot_token:
        payload_data = verify_token(iot_token)
        if payload_data and payload_data.get("type") == "tenant":
            log_audit("tenant", payload_data["sub"], payload_data["email"], "logout",
                      tenant_id=payload_data.get("tenant_id"))
    response.delete_cookie(key="iot_token", path="/")
    return {"ok": True}


class ForgotPasswordRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    recaptcha_token: str


def _send_password_reset_email_safe(to_email: str, tenant_name: str, reset_url: str) -> None:
    """BackgroundTasksから呼ばれる。クライアントは既にレスポンスを受け取っているため、
    送信失敗はログに残すだけで伝播先が無い（既存のsend_trial_expiry_warning_email等と同じ方針）。"""
    try:
        send_password_reset_email(to_email, tenant_name, reset_url)
    except Exception as e:
        print(f"Password reset email send failed: {e}")


def _process_forgot_password(tenant_slug: str, email: str) -> None:
    """テナント・ユーザーの検索からトークン作成・メール送信までをまとめてBackgroundTaskで行う。
    forgot_password本体がDBに一切触れずに即座にレスポンスを返せるようにするため
    （見つかった/見つからない経路の応答速度差で存在確認に使われるのを防ぐ）。"""
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.slug == tenant_slug, Tenant.status == "active",
        ).first()
        if not tenant:
            return
        schema = f"tenant_{str(tenant.id).replace('-', '_')}"
        with engine.connect() as conn:
            row = conn.execute(
                text(f'SELECT id, email FROM "{schema}".users WHERE email = :email AND is_active = TRUE'),
                {"email": email},
            ).fetchone()
        if not row:
            return
        existing = db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == row.id,
        ).first()
        if existing:
            db.delete(existing)
            db.flush()
        token = _secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            id=_audit_uuid.uuid4(), tenant_id=tenant.id, user_id=row.id,
            email=row.email, token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()
        reset_url = f"https://{settings.platform_domain}/admin/reset-password.html?token={token}"
    _send_password_reset_email_safe(row.email, tenant.name, reset_url)


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, request: Request, background_tasks: BackgroundTasks):
    ip = request.client.host if request.client else "unknown"
    rate_key = f"tenant_password_reset:{ip}"
    # 失敗時だけでなく全リクエストを数える。失敗時のみ数えると、429の出方の違いで
    # テナント/メールアドレスの存在確認に使われてしまうため。
    record_failure(rate_key)
    if is_rate_limited(rate_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    if verify_recaptcha(req.recaptcha_token, ip):
        background_tasks.add_task(_process_forgot_password, req.tenant_slug, req.email)
    else:
        print(f"forgot-password: reCAPTCHA check failed for ip={ip}")

    return {"status": "requested"}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(req: ResetPasswordRequest):
    with SessionLocal() as db:
        token_row = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == req.token,
        ).first()
        if not token_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or already used token")

        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            db.delete(token_row)
            db.commit()
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Reset link expired")

        tenant = db.query(Tenant).filter(Tenant.id == token_row.tenant_id).first()
        if not tenant or tenant.status != "active":
            db.delete(token_row)
            db.commit()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or already used token")

        schema = f"tenant_{str(token_row.tenant_id).replace('-', '_')}"
        with engine.connect() as conn:
            user_row = conn.execute(
                text(f'SELECT id FROM "{schema}".users WHERE id = :uid AND is_active = TRUE'),
                {"uid": str(token_row.user_id)},
            ).fetchone()
        if not user_row:
            db.delete(token_row)
            db.commit()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or already used token")

        validate_password(req.new_password, token_row.email)

        new_hash = hash_password(req.new_password)
        with engine.connect() as conn:
            conn.execute(
                text(f'UPDATE "{schema}".users SET password_hash = :hash WHERE id = :uid'),
                {"hash": new_hash, "uid": str(token_row.user_id)},
            )
            conn.commit()

        tenant_id_str = str(token_row.tenant_id)
        user_id_str = str(token_row.user_id)
        email = token_row.email
        db.delete(token_row)
        db.commit()

    forget_session(tenant_id_str, user_id_str)
    log_audit("tenant", user_id_str, email, "password_reset", tenant_id=tenant_id_str)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.get("/me")
def get_me(payload: dict = Depends(require_tenant_session)):
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


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(req: ChangePasswordRequest, response: Response, payload: dict = Depends(require_tenant_session)):
    validate_password(req.new_password, payload.get("email"))

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

    new_hash = hash_password(req.new_password)
    with engine.connect() as conn:
        conn.execute(
            text(f'UPDATE "{schema}".users SET password_hash = :hash WHERE id = :uid'),
            {"hash": new_hash, "uid": user_id},
        )
        conn.commit()
    forget_session(tenant_id, user_id)
    # 他の端末のセッションはこれで失効する。本人の今のセッションは、新しいパスワードの指紋で発行し直して継続させる
    token = create_access_token(
        {"sub": user_id, "email": payload["email"], "type": "tenant", "tenant_id": tenant_id,
         "role": payload["role"], "pwv": password_fingerprint(new_hash)},
        expires_delta=timedelta(hours=settings.grafana_session_expire_hours),
    )
    response.set_cookie(
        key="iot_token", value=token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.grafana_session_expire_hours * 3600, path="/",
    )
    log_audit("tenant", user_id, payload["email"], "change_password",
              tenant_id=tenant_id, resource_type="tenant_user", resource_id=payload["email"])
