import re
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.schemas.device_group import GroupCreate, GroupOut, GroupUpdate
from app.services.auth import verify_token
from app.services.audit import log_audit
from app.services.device_groups import (
    GroupInUseError,
    GroupNameConflictError,
    GroupNotFoundError,
    create_group,
    delete_group,
    list_groups,
    update_group,
)

router = APIRouter(prefix="/tenants/{tenant_id}/groups", tags=["device-groups"])
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _schema(tenant_id: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")
    return f"tenant_{tenant_id.replace('-', '_')}"


def _validate_uuid(value: str, field: str = "id") -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid {field}")
    return value.lower()


@router.get("", response_model=list[GroupOut])
def list_device_groups(tenant_id: str, _: dict = Depends(_require_platform)):
    return list_groups(_schema(tenant_id))


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_device_group(tenant_id: str, body: GroupCreate, payload: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    try:
        group = create_group(schema, body.name, body.description)
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("platform", payload["sub"], payload["email"], "create_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group["id"],
              detail={"name": group["name"]})
    return group


@router.patch("/{group_id}", response_model=GroupOut)
def update_device_group(tenant_id: str, group_id: str, body: GroupUpdate, payload: dict = Depends(_require_platform)):
    group_id = _validate_uuid(group_id, "group_id")
    schema = _schema(tenant_id)
    try:
        group = update_group(schema, group_id, body.name, body.description)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("platform", payload["sub"], payload["email"], "update_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id,
              detail=body.model_dump(exclude_none=True))
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device_group(tenant_id: str, group_id: str, payload: dict = Depends(_require_platform)):
    group_id = _validate_uuid(group_id, "group_id")
    schema = _schema(tenant_id)
    try:
        delete_group(schema, group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupInUseError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "group_in_use", "alert_rules": e.rules})
    log_audit("platform", payload["sub"], payload["email"], "delete_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id)
