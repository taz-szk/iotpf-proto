from datetime import timedelta
from fastapi import APIRouter, HTTPException, Request, Response, status
from app.schemas.auth import LoginRequest, TokenOut, RefreshRequest
from app.services.auth import verify_password, create_access_token, create_refresh_token, verify_token
from app.models.public import PlatformUser
from app.database import SessionLocal
from app.config import settings

router = APIRouter(prefix="/auth")

@router.post("/login", response_model=TokenOut)
def login(req: LoginRequest, response: Response):
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.email == req.email).first()
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    payload = {"sub": str(user.id), "email": user.email, "type": "platform"}
    access = create_access_token(payload)
    refresh = create_refresh_token(payload)
    # Grafana SSO Cookie (24h)
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
    new_payload = {"sub": payload["sub"], "email": payload["email"], "type": payload["type"]}
    return TokenOut(
        access_token=create_access_token(new_payload),
        refresh_token=create_refresh_token(new_payload),
    )


@router.get("/verify-jwt")
def verify_jwt(request: Request, response: Response):
    token = request.cookies.get("iot_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token")
    payload = verify_token(token)
    if not payload or payload.get("type") not in ("tenant", "platform") or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    response.headers["X-Auth-User"] = payload["email"]
    return {"email": payload["email"], "type": payload["type"]}
