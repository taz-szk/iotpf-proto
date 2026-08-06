from fastapi import APIRouter, HTTPException, status
from app.schemas.auth import LoginRequest, TokenOut, RefreshRequest
from app.services.auth import verify_password, create_access_token, create_refresh_token, verify_token
from app.models.public import PlatformUser
from app.database import SessionLocal

router = APIRouter(prefix="/auth")

@router.post("/login", response_model=TokenOut)
def login(req: LoginRequest):
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.email == req.email).first()
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    payload = {"sub": str(user.id), "email": user.email, "type": "platform"}
    return TokenOut(
        access_token=create_access_token(payload),
        refresh_token=create_refresh_token(payload),
    )

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
