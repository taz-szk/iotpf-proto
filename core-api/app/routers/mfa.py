from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.services.auth import verify_token, create_access_token, create_refresh_token, verify_password
from app.services.totp import generate_totp_secret, get_totp_uri, verify_totp_code
from app.models.public import PlatformUser
from app.database import SessionLocal

router = APIRouter(prefix="/auth/totp", tags=["mfa"])
_bearer = HTTPBearer()


def _require_partial_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "partial_platform" or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _full_payload(user: PlatformUser) -> dict:
    return {
        "sub": str(user.id),
        "email": user.email,
        "type": "platform",
        "tok_ver": user.token_version,
    }


class TotpCode(BaseModel):
    code: str

class DisableTotpRequest(BaseModel):
    password: str


@router.get("/setup")
def totp_setup(payload: dict = Depends(_require_partial_platform)):
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.id == payload["sub"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.totp_enabled:
            raise HTTPException(status_code=404, detail="TOTP already active")
        if not user.totp_secret:
            user.totp_secret = generate_totp_secret()
            db.commit()
            db.refresh(user)
        secret = user.totp_secret
    uri = get_totp_uri(secret, payload["email"])
    return {"otpauth_uri": uri, "secret": secret}


@router.post("/activate")
def totp_activate(body: TotpCode, payload: dict = Depends(_require_partial_platform)):
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.id == payload["sub"]).first()
        if not user or not user.totp_secret:
            raise HTTPException(status_code=400, detail="TOTP not initialized")
        if not verify_totp_code(user.totp_secret, body.code):
            raise HTTPException(status_code=400, detail="Invalid TOTP code")
        user.totp_enabled = True
        db.commit()
        db.refresh(user)
        full = _full_payload(user)
    from app.services.grafana import ensure_platform_admin_in_grafana
    try:
        ensure_platform_admin_in_grafana(user.email)
    except Exception:
        pass
    return {"access_token": create_access_token(full), "refresh_token": create_refresh_token(full), "token_type": "bearer"}


@router.post("/verify")
def totp_verify(body: TotpCode, payload: dict = Depends(_require_partial_platform)):
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.id == payload["sub"]).first()
        if not user or not user.totp_enabled or not user.totp_secret:
            raise HTTPException(status_code=400, detail="TOTP not configured")
        if not verify_totp_code(user.totp_secret, body.code):
            raise HTTPException(status_code=400, detail="Invalid TOTP code")
        full = _full_payload(user)
    from app.services.grafana import ensure_platform_admin_in_grafana
    try:
        ensure_platform_admin_in_grafana(user.email)
    except Exception:
        pass
    return {"access_token": create_access_token(full), "refresh_token": create_refresh_token(full), "token_type": "bearer"}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def totp_disable(body: DisableTotpRequest, creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.id == payload["sub"]).first()
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=400, detail="Invalid password")
        user.totp_enabled = False
        user.totp_secret = None
        user.token_version = user.token_version + 1
        db.commit()
