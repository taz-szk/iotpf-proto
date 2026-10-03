"""テナントポータル向けSORACOM回線管理API。認証情報(Auth Key)はadmin限定、回線の確認・操作・
紐づけはadmin/operatorに許可する。"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status as http_status
from pydantic import BaseModel, Field

from app.services.audit import log_audit
from app.services.sim_service import (
    BindingNotFoundError,
    DeviceAlreadyBoundError,
    DeviceNotFoundError,
    SimAlreadyBoundError,
    SoracomNotConfiguredError,
    activate_line,
    bind_line,
    deactivate_line,
    get_line,
    list_lines,
    set_speed_class,
    unbind_line,
)
from app.services.soracom_client import SoracomApiError, SoracomAuthError, verify_credentials
from app.services.soracom_credentials import delete_credentials, get_credentials_summary, save_credentials
from app.services.tenant_session import require_tenant_session

router = APIRouter(prefix="/tenant-portal/me/sim", tags=["tenant-sim"])

_require_tenant = require_tenant_session

# IMSIは14〜15桁の数字のみ(SORACOMの実際のIMSI形式)。FastAPI/Starletteの既定の{imsi}パス変換は
# デコード済みの?・#・%2Fなどをそのまま受け取ってしまい、httpx/上流SORACOM API呼び出し時に
# クエリ文字列・フラグメント・エンコード済みスラッシュとして再解釈されうる(パス/クエリ改変の恐れ)。
# ここで桁数の数字のみに制約し、サービス層・SORACOM呼び出しに届く前に422で弾く。
_IMSI_PATH = Path(..., pattern=r"^\d{14,15}$")


def _require_admin(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin",):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return payload


def _require_admin_or_operator(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="Operator or admin role required")
    return payload


class CredentialsUpdate(BaseModel):
    coverage: Literal["jp", "g"] = "jp"
    auth_key_id: Optional[str] = None
    auth_key: Optional[str] = None


@router.get("/credentials")
def get_credentials_endpoint(payload: dict = Depends(_require_admin_or_operator)):
    return get_credentials_summary(payload["tenant_id"])


@router.put("/credentials", status_code=http_status.HTTP_204_NO_CONTENT)
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


@router.delete("/credentials", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_credentials_endpoint(payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    delete_credentials(tenant_id)
    log_audit("tenant", payload["sub"], payload["email"], "delete_soracom_credentials",
              tenant_id=tenant_id, resource_type="soracom_credentials", resource_id="credentials")


def _not_configured_as_400():
    raise HTTPException(status_code=400, detail="SORACOM連携が設定されていません")


@router.get("/lines")
def list_lines_endpoint(
    status: Optional[str] = None, speed_class: Optional[str] = None,
    tag_name: Optional[str] = None, tag_value: Optional[str] = None,
    bound: Optional[bool] = None, cursor: Optional[str] = None, limit: int = 20,
    payload: dict = Depends(_require_admin_or_operator),
):
    try:
        return list_lines(
            payload["tenant_id"], status=status, speed_class=speed_class,
            tag_name=tag_name, tag_value=tag_value, bound=bound, cursor=cursor, limit=limit,
        )
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    except SoracomApiError as e:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/lines/{imsi}")
def get_line_endpoint(imsi: str = _IMSI_PATH, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = get_line(payload["tenant_id"], imsi)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    except (SoracomAuthError, SoracomApiError) as e:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(e))
    if line is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Line not found")
    return line


@router.post("/lines/{imsi}/activate")
def activate_line_endpoint(imsi: str = _IMSI_PATH, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = activate_line(payload["tenant_id"], imsi)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    except (SoracomAuthError, SoracomApiError) as e:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(e))
    if line is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Line not found")
    log_audit("tenant", payload["sub"], payload["email"], "activate_sim",
              tenant_id=payload["tenant_id"], resource_type="sim", resource_id=imsi)
    return line


@router.post("/lines/{imsi}/deactivate")
def deactivate_line_endpoint(imsi: str = _IMSI_PATH, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = deactivate_line(payload["tenant_id"], imsi)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    except (SoracomAuthError, SoracomApiError) as e:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(e))
    if line is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Line not found")
    log_audit("tenant", payload["sub"], payload["email"], "deactivate_sim",
              tenant_id=payload["tenant_id"], resource_type="sim", resource_id=imsi)
    return line


class SpeedClassUpdate(BaseModel):
    speed_class: str = Field(min_length=1, max_length=50)


@router.post("/lines/{imsi}/speed-class")
def set_speed_class_endpoint(
    body: SpeedClassUpdate, imsi: str = _IMSI_PATH, payload: dict = Depends(_require_admin_or_operator),
):
    try:
        line = set_speed_class(payload["tenant_id"], imsi, body.speed_class)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    except (SoracomAuthError, SoracomApiError) as e:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(e))
    if line is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Line not found")
    log_audit("tenant", payload["sub"], payload["email"], "update_sim_speed_class",
              tenant_id=payload["tenant_id"], resource_type="sim", resource_id=imsi,
              detail={"speed_class": body.speed_class})
    return line


class BindRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=255)
    iccid: Optional[str] = None


@router.post("/lines/{imsi}/bind", status_code=http_status.HTTP_204_NO_CONTENT)
def bind_line_endpoint(
    body: BindRequest, imsi: str = _IMSI_PATH, payload: dict = Depends(_require_admin_or_operator),
):
    tenant_id = payload["tenant_id"]
    try:
        bind_line(tenant_id, imsi, body.device_id, iccid=body.iccid)
    except DeviceNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    except (SimAlreadyBoundError, DeviceAlreadyBoundError) as e:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(e))
    log_audit("tenant", payload["sub"], payload["email"], "bind_sim",
              tenant_id=tenant_id, resource_type="sim", resource_id=imsi,
              detail={"device_id": body.device_id})


@router.delete("/lines/{imsi}/bind", status_code=http_status.HTTP_204_NO_CONTENT)
def unbind_line_endpoint(imsi: str = _IMSI_PATH, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    try:
        unbind_line(tenant_id, imsi)
    except BindingNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Binding not found")
    log_audit("tenant", payload["sub"], payload["email"], "unbind_sim",
              tenant_id=tenant_id, resource_type="sim", resource_id=imsi)
