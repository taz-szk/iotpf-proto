from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.models.public import MfaSettings
from app.services.auth import verify_token
from app.database import SessionLocal

router = APIRouter(prefix="/platform", tags=["platform"])
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


class MfaSettingsUpdate(BaseModel):
    platform_required: bool | None = None
    tenant_required: bool | None = None


@router.get("/mfa-settings")
def get_mfa_settings(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        s = db.query(MfaSettings).filter(MfaSettings.id == 1).first()
        if not s:
            return {"platform_required": False, "tenant_required": False}
        return {"platform_required": s.platform_required, "tenant_required": s.tenant_required}


@router.patch("/mfa-settings")
def update_mfa_settings(body: MfaSettingsUpdate, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        s = db.query(MfaSettings).filter(MfaSettings.id == 1).first()
        if not s:
            raise HTTPException(status_code=500, detail="MFA settings not initialized")
        if body.platform_required is not None:
            s.platform_required = body.platform_required
        if body.tenant_required is not None:
            s.tenant_required = body.tenant_required
        db.commit()
        db.refresh(s)
        return {"platform_required": s.platform_required, "tenant_required": s.tenant_required}
