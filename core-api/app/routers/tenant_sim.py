"""テナントポータル向けSORACOM回線管理API。認証情報(Auth Key)はadmin限定、回線の確認・操作・
紐づけはadmin/operatorに許可する。"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.services.audit import log_audit
from app.services.soracom_client import verify_credentials
from app.services.soracom_credentials import delete_credentials, get_credentials_summary, save_credentials
from app.services.tenant_session import require_tenant_session

router = APIRouter(prefix="/tenant-portal/me/sim", tags=["tenant-sim"])

_require_tenant = require_tenant_session


def _require_admin(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin",):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return payload


def _require_admin_or_operator(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator or admin role required")
    return payload


class CredentialsUpdate(BaseModel):
    coverage: Literal["jp", "g"] = "jp"
    auth_key_id: Optional[str] = None
    auth_key: Optional[str] = None


@router.get("/credentials")
def get_credentials_endpoint(payload: dict = Depends(_require_admin_or_operator)):
    return get_credentials_summary(payload["tenant_id"])


@router.put("/credentials", status_code=status.HTTP_204_NO_CONTENT)
def put_credentials(body: CredentialsUpdate, payload: dict = Depends(_require_admin)):
    if (body.auth_key_id is None) != (body.auth_key is None):
        raise HTTPException(status_code=422, detail="auth_key_id and auth_key must be provided together")

    tenant_id = payload["tenant_id"]
    if body.auth_key_id is not None:
        if not verify_credentials(tenant_id, body.coverage, body.auth_key_id, body.auth_key):
            raise HTTPException(status_code=422, detail="SORACOMの認証情報が正しくありません")

    save_credentials(tenant_id, body.coverage, body.auth_key_id, body.auth_key)
    log_audit("tenant", payload["sub"], payload["email"], "save_soracom_credentials",
              tenant_id=tenant_id, resource_type="soracom_credentials", resource_id="credentials")


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
def delete_credentials_endpoint(payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    delete_credentials(tenant_id)
    log_audit("tenant", payload["sub"], payload["email"], "delete_soracom_credentials",
              tenant_id=tenant_id, resource_type="soracom_credentials", resource_id="credentials")
