from datetime import timedelta
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import text
from app.services.auth import verify_token, create_access_token, verify_password
from app.services.totp import generate_totp_secret, get_totp_uri, verify_totp_code
from app.services.grafana import ensure_grafana_user_in_org, set_user_default_org_via_proxy
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.config import settings

router = APIRouter(prefix="/tenant-auth/totp", tags=["tenant-mfa"])
_bearer = HTTPBearer()


def _require_partial_tenant(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "partial_tenant" or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _schema(tenant_id: str) -> str:
    return f"tenant_{tenant_id.replace('-', '_')}"


def _set_tenant_cookie(response: Response, payload: dict) -> None:
    token = create_access_token(payload, expires_delta=timedelta(hours=settings.grafana_session_expire_hours))
    response.set_cookie(
        key="iot_token", value=token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.grafana_session_expire_hours * 3600, path="/",
    )


class TotpCode(BaseModel):
    code: str

class DisableTotpRequest(BaseModel):
    password: str


@router.get("/setup")
def tenant_totp_setup(payload: dict = Depends(_require_partial_tenant)):
    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT totp_secret, totp_enabled FROM "{schema}".users WHERE id = :uid'),
            {"uid": user_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="TOTP already active")
    secret = row.totp_secret
    if not secret:
        secret = generate_totp_secret()
        with engine.connect() as conn:
            conn.execute(
                text(f'UPDATE "{schema}".users SET totp_secret = :s WHERE id = :uid'),
                {"s": secret, "uid": user_id},
            )
            conn.commit()
    return {"otpauth_uri": get_totp_uri(secret, payload["email"]), "secret": secret}


@router.post("/activate")
def tenant_totp_activate(body: TotpCode, response: Response, payload: dict = Depends(_require_partial_tenant)):
    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT totp_secret FROM "{schema}".users WHERE id = :uid'),
            {"uid": user_id},
        ).fetchone()
    if not row or not row.totp_secret:
        raise HTTPException(status_code=400, detail="TOTP not initialized")
    if not verify_totp_code(row.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    with engine.connect() as conn:
        conn.execute(
            text(f'UPDATE "{schema}".users SET totp_enabled = TRUE WHERE id = :uid'),
            {"uid": user_id},
        )
        conn.commit()
    # Grafana sync（non-fatal）
    try:
        with SessionLocal() as db:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant and tenant.grafana_org_id:
            g_org_id = int(tenant.grafana_org_id)
            g_login = f"{tenant_id}:{payload['email']}"
            ensure_grafana_user_in_org(g_org_id, payload["email"], "Viewer", tenant_id)
            set_user_default_org_via_proxy(g_login, g_org_id)
    except Exception as e:
        print(f"[tenant_totp_activate] grafana sync warning (non-fatal): {e}")

    full_payload = {
        "sub": user_id,
        "email": payload["email"],
        "type": "tenant",
        "tenant_id": tenant_id,
        "role": payload["role"],
    }
    _set_tenant_cookie(response, full_payload)
    return {
        "status": "ok",
        "user_id": user_id,
        "email": payload["email"],
        "role": payload["role"],
        "tenant_id": tenant_id,
        "redirect_url": "/admin/tenant-portal.html",
    }


@router.post("/verify")
def tenant_totp_verify(body: TotpCode, response: Response, payload: dict = Depends(_require_partial_tenant)):
    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT totp_secret, totp_enabled FROM "{schema}".users WHERE id = :uid'),
            {"uid": user_id},
        ).fetchone()
    if not row or not row.totp_enabled or not row.totp_secret:
        raise HTTPException(status_code=400, detail="TOTP not configured")
    if not verify_totp_code(row.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    # Grafana sync（non-fatal）
    try:
        with SessionLocal() as db:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant and tenant.grafana_org_id:
            g_org_id = int(tenant.grafana_org_id)
            g_login = f"{tenant_id}:{payload['email']}"
            ensure_grafana_user_in_org(g_org_id, payload["email"], "Viewer", tenant_id)
            set_user_default_org_via_proxy(g_login, g_org_id)
    except Exception as e:
        print(f"[tenant_totp_verify] grafana sync warning (non-fatal): {e}")

    full_payload = {
        "sub": user_id,
        "email": payload["email"],
        "type": "tenant",
        "tenant_id": tenant_id,
        "role": payload["role"],
    }
    _set_tenant_cookie(response, full_payload)
    return {
        "status": "ok",
        "user_id": user_id,
        "email": payload["email"],
        "role": payload["role"],
        "tenant_id": tenant_id,
        "redirect_url": "/admin/tenant-portal.html",
    }


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def tenant_totp_disable(body: DisableTotpRequest, iot_token: str = Cookie(default=None)):
    if not iot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(iot_token)
    if not payload or payload.get("type") != "tenant":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT password_hash FROM "{schema}".users WHERE id = :uid'),
            {"uid": user_id},
        ).fetchone()
    if not row or not verify_password(body.password, row.password_hash):
        raise HTTPException(status_code=400, detail="Invalid password")
    with engine.connect() as conn:
        conn.execute(
            text(f'UPDATE "{schema}".users SET totp_enabled = FALSE, totp_secret = NULL WHERE id = :uid'),
            {"uid": user_id},
        )
        conn.commit()
