from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.schemas.auth import LoginRequest, TokenOut, RefreshRequest
from app.services.auth import verify_password, hash_password, create_access_token, create_refresh_token, verify_token
from app.services.rate_limiter import is_rate_limited, record_failure, clear_failures
from app.services.token_blocklist import revoke_jti, is_revoked
from app.models.public import PlatformUser
from app.database import SessionLocal
from app.config import settings
from app.services.grafana import ensure_platform_admin_in_grafana

router = APIRouter(prefix="/auth")
_bearer = HTTPBearer()


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@router.post("/login", response_model=TokenOut)
def login(req: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    rate_key = f"platform_login:{ip}"
    if is_rate_limited(rate_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.email == req.email).first()
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        record_failure(rate_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    clear_failures(rate_key)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "type": "platform",
        "tok_ver": user.token_version,
    }
    try:
        ensure_platform_admin_in_grafana(user.email)
    except Exception:
        pass  # Grafana 未起動でもログインはブロックしない
    access = create_access_token(payload)
    refresh = create_refresh_token(payload)
    grafana_token = create_access_token(
        payload,
        expires_delta=timedelta(hours=settings.grafana_session_expire_hours),
    )
    response.set_cookie(
        key="iot_token", value=grafana_token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.grafana_session_expire_hours * 3600, path="/",
    )
    return TokenOut(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenOut)
def refresh(req: RefreshRequest):
    payload = verify_token(req.refresh_token)
    if not payload or payload.get("token_type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    jti = payload.get("jti")
    if not jti or is_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if payload.get("tok_ver") != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    # ローテーション: 使用済み JTI を失効させる
    exp = payload.get("exp", 0)
    ttl = max(0.0, exp - datetime.now(timezone.utc).timestamp())
    revoke_jti(jti, ttl)

    new_payload = {
        "sub": payload["sub"],
        "email": payload["email"],
        "type": payload["type"],
        "tok_ver": user.token_version,
    }
    return TokenOut(
        access_token=create_access_token(new_payload),
        refresh_token=create_refresh_token(new_payload),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(req: LogoutRequest, response: Response):
    if req.refresh_token:
        payload = verify_token(req.refresh_token)
        if payload and payload.get("token_type") == "refresh":
            jti = payload.get("jti")
            if jti:
                exp = payload.get("exp", 0)
                ttl = max(0.0, exp - datetime.now(timezone.utc).timestamp())
                revoke_jti(jti, ttl)
    response.delete_cookie(key="iot_token", path="/")


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    req: ChangePasswordRequest,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.id == payload["sub"]).first()
        if not user or not verify_password(req.current_password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="現在のパスワードが正しくありません")
        user.password_hash = hash_password(req.new_password)
        user.token_version = user.token_version + 1
        db.commit()


@router.get("/verify-jwt")
def verify_jwt(request: Request, response: Response):
    token = request.cookies.get("iot_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token")
    payload = verify_token(token)
    if not payload or payload.get("type") not in ("tenant", "platform") or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    email = payload["email"]
    # Defense-in-depth: Grafana の予約済みユーザー名はサーバー管理者権限になるため拒否
    _RESERVED = {"admin@localhost", "admin@grafana", "grafana@grafana"}
    if not email.isascii() or email.lower() in _RESERVED:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("type") == "tenant":
        tenant_id = payload.get("tenant_id", "")
        x_auth_user = f"{tenant_id}:{email}"
    else:
        x_auth_user = email
    response.headers["X-Auth-User"] = x_auth_user
    response.headers["X-Auth-Email"] = email
    return {"email": email, "type": payload["type"]}
