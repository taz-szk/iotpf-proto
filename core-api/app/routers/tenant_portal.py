"""テナントユーザー（Cookie認証）向けの自テナント管理API。"""
import hashlib
import re
import secrets
import time
import uuid as uuid_lib
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Literal

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text, bindparam, ARRAY, String as SaString

from app.database import SessionLocal, engine, add_firmware_tables_to_tenant_schema
from app.models.public import AuditLog, ProvisioningToken, Tenant
from app.schemas.audit import AuditLogListOut, AuditLogOut
from app.schemas.device_group import GroupCreate, GroupOut, GroupUpdate
from app.services.auth import hash_password, verify_password, verify_token
from app.services.grafana import retire_device_in_influxdb
from app.services.audit import write_audit_log, log_audit
from app.routers.stats import _count_influxdb_points
from app.services.device_groups import (
    DeviceNotFoundError,
    GroupInUseError,
    GroupNameConflictError,
    GroupNotFoundError,
    assign_device_group,
    create_group,
    delete_group,
    group_exists,
    list_group_device_ids,
    list_groups,
    update_group,
)
from app.services.tenant import teardown_tenant
from app.services.emqx_publisher import publish_ota_command
from app.services.minio_client import (
    create_firmware_download_token,
    delete_firmware,
    upload_firmware,
)
from app.config import settings

router = APIRouter(prefix="/tenant-portal", tags=["tenant-portal"])


# ---------------------------------------------------------------------------
# 共通認証 Dependency
# ---------------------------------------------------------------------------

def _require_tenant(iot_token: str = Cookie(default=None)) -> dict:
    if not iot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(iot_token)
    if not payload or payload.get("type") != "tenant" or payload.get("public"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return payload


def _require_admin(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin",):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return payload


def _require_admin_or_operator(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator or admin role required")
    return payload


def _schema(tenant_id: str) -> str:
    return f"tenant_{tenant_id.replace('-', '_')}"


def _validate_uuid(value: str, field: str = "id") -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid {field}")
    return value.lower()


# ---------------------------------------------------------------------------
# 自テナント情報
# ---------------------------------------------------------------------------

@router.get("/me")
def get_me(payload: dict = Depends(_require_tenant)):
    from app.services.grafana import get_org_home_dashboard_url
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    grafana_org_id = tenant.grafana_org_id
    grafana_url = None
    if grafana_org_id:
        try:
            dashboard_path = get_org_home_dashboard_url(int(grafana_org_id))
            if dashboard_path:
                grafana_url = f"{dashboard_path}?orgId={grafana_org_id}&kiosk&theme=light"
            else:
                grafana_url = f"/grafana/?orgId={grafana_org_id}&kiosk&theme=light"
        except Exception:
            grafana_url = f"/grafana/?orgId={grafana_org_id}&kiosk&theme=light"
    public_token = tenant.public_token
    return {
        "user_id": payload["sub"],
        "email": payload["email"],
        "role": payload["role"],
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "tenant_slug": tenant.slug,
        "grafana_org_id": grafana_org_id,
        "grafana_url": grafana_url,
        "public_token": public_token,
        "public_url": f"/public/{public_token}" if public_token else None,
    }


# ---------------------------------------------------------------------------
# 公開ダッシュボードアクセス管理
# ---------------------------------------------------------------------------

class PublicAccessBody(BaseModel):
    enable: bool


@router.post("/me/public-access")
def toggle_public_access(body: PublicAccessBody, payload: dict = Depends(_require_admin)):
    """公開ダッシュボードURLを有効化/無効化する（管理者のみ）。"""
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if body.enable:
            if not tenant.public_token:
                tenant.public_token = str(uuid_lib.uuid4())
        else:
            tenant.public_token = None
        db.commit()
        token = tenant.public_token
    return {
        "public_token": token,
        "public_url": f"/public/{token}" if token else None,
    }


# ---------------------------------------------------------------------------
# デバイス管理
# ---------------------------------------------------------------------------

@router.get("/me/devices")
def list_devices(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(text(f'''
            SELECT id, device_id, device_name, connection_status, last_seen_at,
                   fw_version, cert_not_after, created_at, group_id
            FROM "{schema}".devices
            ORDER BY created_at DESC
            LIMIT 1000
        ''')).fetchall()
    return [
        {
            "id": str(r.id),
            "device_id": r.device_id,
            "device_name": r.device_name or r.device_id,
            "connection_status": r.connection_status,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "fw_version": r.fw_version,
            "cert_not_after": r.cert_not_after.isoformat() if r.cert_not_after else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "group_id": str(r.group_id) if r.group_id else None,
        }
        for r in rows
    ]


@router.delete("/me/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: str, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT device_name FROM "{schema}".devices WHERE device_id = :did'),
            {"did": device_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        device_name = row.device_name or device_id
        conn.execute(text(f'DELETE FROM "{schema}".devices WHERE device_id = :did'), {"did": device_id})
        conn.commit()
    log_audit("tenant", payload["sub"], payload["email"], "delete_device",
              tenant_id=tenant_id, resource_type="device", resource_id=device_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant and tenant.influxdb_org_id:
        retire_device_in_influxdb(tenant.influxdb_org_id, device_name)


class DeviceUpdateBody(BaseModel):
    group_id: Optional[str] = None


@router.patch("/me/devices/{device_id}")
def update_device(device_id: str, body: DeviceUpdateBody, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    if body.group_id is not None:
        body.group_id = _validate_uuid(body.group_id, "group_id")
    try:
        old_group_id = assign_device_group(schema, device_id, body.group_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    log_audit("tenant", payload["sub"], payload["email"], "assign_device_group",
              tenant_id=tenant_id, resource_type="device", resource_id=device_id,
              detail={"old_group_id": old_group_id, "new_group_id": body.group_id})
    with engine.connect() as conn:
        row = conn.execute(text(f'''
            SELECT id, device_id, device_name, connection_status, last_seen_at,
                   fw_version, cert_not_after, created_at, group_id
            FROM "{schema}".devices WHERE device_id = :did
        '''), {"did": device_id}).fetchone()
    return {
        "id": str(row.id), "device_id": row.device_id, "device_name": row.device_name or row.device_id,
        "connection_status": row.connection_status,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "fw_version": row.fw_version,
        "cert_not_after": row.cert_not_after.isoformat() if row.cert_not_after else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "group_id": str(row.group_id) if row.group_id else None,
    }


# ---------------------------------------------------------------------------
# ユーザー管理
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: Literal["admin", "operator", "viewer"] = "viewer"


@router.get("/me/users")
def list_users(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT id, email, role, is_active, created_at FROM "{schema}".users ORDER BY created_at DESC')
        ).fetchall()
    return [
        {
            "id": str(r.id),
            "email": r.email,
            "role": r.role,
            "is_active": r.is_active,
            "created_at": str(r.created_at) if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/me/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, payload: dict = Depends(_require_admin)):
    from app.services.grafana import ensure_grafana_user_in_org
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    user_id = str(uuid_lib.uuid4())
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    with engine.connect() as conn:
        try:
            conn.execute(
                text(f'''INSERT INTO "{schema}".users (id, email, password_hash, role)
                         VALUES (:id, :email, :hash, :role)'''),
                {"id": user_id, "email": body.email, "hash": hash_password(body.password), "role": body.role},
            )
            conn.commit()
        except Exception:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    if tenant and tenant.grafana_org_id:
        try:
            ensure_grafana_user_in_org(int(tenant.grafana_org_id), body.email, "Viewer", tenant_id)
        except Exception:
            pass
    log_audit("tenant", payload["sub"], payload["email"], "create_tenant_user",
              tenant_id=tenant_id, resource_type="tenant_user", resource_id=body.email)
    return {"id": user_id, "email": body.email, "role": body.role, "is_active": True}


class UserUpdateBody(BaseModel):
    role: Optional[Literal["admin", "operator", "viewer"]] = None
    is_active: Optional[bool] = None


@router.patch("/me/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_user(user_id: str, body: UserUpdateBody, payload: dict = Depends(_require_admin_or_operator)):
    if payload["sub"] == user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot modify yourself")
    if body.role is not None and payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required to change role")
    if body.role is None and body.is_active is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update")
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    sets, params = [], {"uid": user_id}
    if body.role is not None:
        sets.append("role = :role")
        params["role"] = body.role
    if body.is_active is not None:
        sets.append("is_active = :is_active")
        params["is_active"] = body.is_active
    with engine.connect() as conn:
        result = conn.execute(
            text(f'UPDATE "{schema}".users SET {", ".join(sets)} WHERE id = :uid'),
            params,
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        conn.commit()


@router.delete("/me/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, payload: dict = Depends(_require_admin_or_operator)):
    if payload["sub"] == user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete yourself")
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        result = conn.execute(
            text(f'DELETE FROM "{schema}".users WHERE id = :uid'),
            {"uid": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        conn.commit()
    log_audit("tenant", payload["sub"], payload["email"], "delete_tenant_user",
              tenant_id=tenant_id, resource_type="tenant_user", resource_id=user_id)


class PasswordResetBody(BaseModel):
    password: str = Field(min_length=8)


@router.patch("/me/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(user_id: str, body: PasswordResetBody, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        result = conn.execute(
            text(f'UPDATE "{schema}".users SET password_hash = :hash WHERE id = :uid'),
            {"hash": hash_password(body.password), "uid": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        conn.commit()


# ---------------------------------------------------------------------------
# プロビジョニングトークン
# ---------------------------------------------------------------------------

_UNLIMITED_EXPIRES = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
_UNLIMITED_DEVICES = 2_000_000_000

class TokenCreate(BaseModel):
    max_devices: Optional[int] = Field(default=100, gt=0, le=10000)
    expires_days: Optional[int] = Field(default=365, gt=0, le=1825)


@router.get("/me/tokens")
def list_tokens(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    tenant_uuid = uuid_lib.UUID(tenant_id)
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        tokens = db.query(ProvisioningToken).filter(
            ProvisioningToken.tenant_id == tenant_uuid,
            ProvisioningToken.is_active == True,
        ).all()
        result = []
        for t in tokens:
            active_count = db.execute(text(f'''
                SELECT COUNT(*) FROM "{schema}".devices
                WHERE provisioning_token_id = :tid
            '''), {"tid": str(t.id)}).scalar() or 0
            result.append({
                "id": str(t.id),
                "token": t.token,
                "max_devices": None if t.max_devices >= _UNLIMITED_DEVICES else t.max_devices,
                "registered_count": t.registered_count,
                "active_count": int(active_count),
                "deleted_count": max(0, t.registered_count - int(active_count)),
                "expires_at": None if t.expires_at >= _UNLIMITED_EXPIRES else t.expires_at.isoformat(),
                "is_active": t.is_active,
            })
        return result


@router.post("/me/tokens", status_code=status.HTTP_201_CREATED)
def create_token(body: TokenCreate, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    tenant_uuid = uuid_lib.UUID(tenant_id)
    with SessionLocal() as db:
        token = ProvisioningToken(
            id=uuid_lib.uuid4(),
            token=secrets.token_urlsafe(32),
            tenant_id=tenant_uuid,
            max_devices=_UNLIMITED_DEVICES if body.max_devices is None else body.max_devices,
            expires_at=_UNLIMITED_EXPIRES if body.expires_days is None
                       else datetime.now(timezone.utc) + timedelta(days=body.expires_days),
        )
        db.add(token)
        write_audit_log(db, "tenant", payload["sub"], payload["email"],
                        "create_provisioning_token", tenant_id=tenant_id,
                        resource_type="provisioning_token", resource_id=str(token.id))
        db.commit()
        db.refresh(token)
        return {
            "id": str(token.id),
            "token": token.token,
            "max_devices": None if token.max_devices >= _UNLIMITED_DEVICES else token.max_devices,
            "registered_count": token.registered_count,
            "expires_at": None if token.expires_at >= _UNLIMITED_EXPIRES else token.expires_at.isoformat(),
            "is_active": token.is_active,
        }


@router.get("/me/tokens/{token_id}/devices")
def list_token_devices(token_id: str, payload: dict = Depends(_require_tenant)):
    _validate_uuid(token_id, "token_id")
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(text(f'''
            SELECT device_id, device_name, connection_status, created_at
            FROM "{schema}".devices
            WHERE provisioning_token_id = :tid
            ORDER BY created_at DESC
        '''), {"tid": token_id}).fetchall()
    return [
        {
            "device_id": r.device_id,
            "device_name": r.device_name or r.device_id,
            "connection_status": r.connection_status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.delete("/me/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: str, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    tenant_uuid = uuid_lib.UUID(tenant_id)
    token_uuid = uuid_lib.UUID(token_id)
    with SessionLocal() as db:
        token = db.query(ProvisioningToken).filter(
            ProvisioningToken.id == token_uuid,
            ProvisioningToken.tenant_id == tenant_uuid,
        ).first()
        if not token:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
        token.is_active = False
        write_audit_log(db, "tenant", payload["sub"], payload["email"],
                        "delete_provisioning_token", tenant_id=tenant_id,
                        resource_type="provisioning_token", resource_id=token_id)
        db.commit()


# ---------------------------------------------------------------------------
# アラートルール
# ---------------------------------------------------------------------------

class AlertRuleCreate(BaseModel):
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    sensor_key: str
    condition: Literal["above", "below", "equal", "device_offline"]
    threshold: Optional[float] = None
    trigger_mode: Literal["consecutive", "duration", "consecutive_and_duration"] = "consecutive"
    consecutive_count: int = 3
    duration_sec: int = 60
    severity: Literal["info", "warning", "critical"] = "warning"
    notify_emails: list[str] = []

    @field_validator("group_id")
    @classmethod
    def _validate_group_id_format(cls, v):
        if v is not None and not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', v.lower()):
            raise ValueError("group_id must be a valid UUID")
        return v

    @model_validator(mode="after")
    def _validate_exclusive_target(self):
        if self.device_id and self.group_id:
            raise ValueError("device_id and group_id are mutually exclusive")
        return self

class AlertRuleUpdate(BaseModel):
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    sensor_key: Optional[str] = None
    condition: Optional[Literal["above", "below", "equal", "device_offline"]] = None
    threshold: Optional[float] = None
    trigger_mode: Optional[Literal["consecutive", "duration", "consecutive_and_duration"]] = None
    consecutive_count: Optional[int] = None
    duration_sec: Optional[int] = None
    severity: Optional[Literal["info", "warning", "critical"]] = None
    notify_emails: Optional[list[str]] = None

    @field_validator("group_id")
    @classmethod
    def _validate_group_id_format(cls, v):
        if v is not None and not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', v.lower()):
            raise ValueError("group_id must be a valid UUID")
        return v

    @model_validator(mode="after")
    def _validate_exclusive_target(self):
        if self.device_id and self.group_id:
            raise ValueError("device_id and group_id are mutually exclusive")
        return self


@router.get("/me/sensor-keys", response_model=list[str])
def list_sensor_keys_portal(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    import httpx
    query = (
        'import "influxdata/influxdb/schema"\n'
        'schema.fieldKeys(\n'
        '  bucket: "telemetry",\n'
        '  predicate: (r) => r._measurement == "telemetry",\n'
        '  start: -30d\n'
        ')'
    )
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant or not tenant.influxdb_org_id or not tenant.influxdb_token:
            return []
        org_id = tenant.influxdb_org_id
        token = tenant.influxdb_token
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={org_id}",
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "type": "flux"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        keys = []
        header = None
        for line in resp.text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split(",")
            if header is None:
                header = parts
                continue
            if "_value" in header:
                v = parts[header.index("_value")].strip()
                if v:
                    keys.append(v)
        return sorted(set(keys))
    except Exception:
        return []


@router.get("/me/alert-rules")
def list_alert_rules(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        rows = db.execute(text(f'''
            SELECT r.id, r.device_id, r.group_id, r.sensor_key, r.condition, r.threshold, r.trigger_mode,
                   r.consecutive_count, r.duration_sec, r.severity, r.notify_emails, r.is_active,
                   MAX(e.triggered_at) AS last_triggered_at
            FROM "{schema}".alert_rules r
            LEFT JOIN "{schema}".alert_events e ON e.rule_id = r.id
            WHERE r.is_active = TRUE
            GROUP BY r.id
        ''')).fetchall()
    return [
        {
            "id": str(r.id),
            "device_id": r.device_id,
            "group_id": str(r.group_id) if r.group_id else None,
            "sensor_key": r.sensor_key,
            "condition": r.condition,
            "threshold": float(r.threshold) if r.threshold is not None else None,
            "trigger_mode": r.trigger_mode,
            "consecutive_count": r.consecutive_count,
            "duration_sec": r.duration_sec,
            "severity": r.severity,
            "notify_emails": list(r.notify_emails) if r.notify_emails else [],
            "is_active": r.is_active,
            "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        }
        for r in rows
    ]


@router.post("/me/alert-rules", status_code=status.HTTP_201_CREATED)
def create_alert_rule(body: AlertRuleCreate, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    if body.group_id is not None and not group_exists(schema, body.group_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    rule_id = str(uuid_lib.uuid4())
    with SessionLocal() as db:
        db.execute(
            text(f'''
                INSERT INTO "{schema}".alert_rules
                  (id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails)
                VALUES (:id, :did, :gid, :sk, :cond, :thr, :tm, :cc, :ds, :sev, :emails)
            ''').bindparams(bindparam("emails", type_=ARRAY(SaString))),
            {
                "id": rule_id, "did": body.device_id, "gid": body.group_id, "sk": body.sensor_key,
                "cond": body.condition, "thr": body.threshold, "tm": body.trigger_mode,
                "cc": body.consecutive_count, "ds": body.duration_sec,
                "sev": body.severity, "emails": list(body.notify_emails),
            }
        )
        db.commit()
    return {"id": rule_id, **body.model_dump()}


@router.patch("/me/alert-rules/{rule_id}")
def update_alert_rule(rule_id: str, body: AlertRuleUpdate, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    _NULLABLE_ALERT_FIELDS = {"device_id", "group_id", "threshold"}
    updates = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k in _NULLABLE_ALERT_FIELDS
    }
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    _ALLOWED_ALERT_COLS = {"sensor_key", "condition", "threshold", "trigger_mode",
                           "consecutive_count", "duration_sec", "severity", "device_id", "group_id"}
    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT id, device_id, group_id FROM "{schema}".alert_rules WHERE id = :rid AND is_active = TRUE
        '''), {"rid": rule_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
        final_device_id = updates.get("device_id", row.device_id)
        final_group_id = updates.get("group_id", row.group_id)
        if final_device_id and final_group_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="device_id and group_id are mutually exclusive")
        if updates.get("group_id") is not None and not group_exists(schema, updates["group_id"]):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        set_clauses = ", ".join(
            f"{col} = :{col}" for col in updates if col != "notify_emails" and col in _ALLOWED_ALERT_COLS
        )
        params = {"rid": rule_id, **{k: v for k, v in updates.items() if k != "notify_emails"}}
        if "notify_emails" in updates:
            set_clauses = (set_clauses + ", notify_emails = :notify_emails").lstrip(", ")
            stmt = text(f'UPDATE "{schema}".alert_rules SET {set_clauses} WHERE id = :rid'
                        ).bindparams(bindparam("notify_emails", type_=ARRAY(SaString)))
            params["notify_emails"] = updates["notify_emails"]
        else:
            stmt = text(f'UPDATE "{schema}".alert_rules SET {set_clauses} WHERE id = :rid')
        db.execute(stmt, params)
        db.commit()
        updated = db.execute(text(f'''
            SELECT id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE id = :rid
        '''), {"rid": rule_id}).fetchone()
    return {
        "id": str(updated.id), "device_id": updated.device_id,
        "group_id": str(updated.group_id) if updated.group_id else None,
        "sensor_key": updated.sensor_key, "condition": updated.condition,
        "threshold": float(updated.threshold) if updated.threshold is not None else None,
        "trigger_mode": updated.trigger_mode, "consecutive_count": updated.consecutive_count,
        "duration_sec": updated.duration_sec, "severity": updated.severity,
        "notify_emails": list(updated.notify_emails) if updated.notify_emails else [],
        "is_active": updated.is_active,
    }


@router.delete("/me/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert_rule(rule_id: str, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        result = db.execute(text(f'''
            UPDATE "{schema}".alert_rules SET is_active = FALSE
            WHERE id = :rid AND is_active = TRUE
        '''), {"rid": rule_id})
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")


# ---------------------------------------------------------------------------
# 統計
# ---------------------------------------------------------------------------

@router.get("/me/stats")
def get_stats(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        total_devices = db.execute(text(f'SELECT COUNT(*) FROM "{schema}".devices')).scalar() or 0
        online_devices = db.execute(
            text(f"SELECT COUNT(*) FROM \"{schema}\".devices WHERE connection_status = 'online'")
        ).scalar() or 0
        alert_events_this_month = db.execute(
            text(f"SELECT COUNT(*) FROM \"{schema}\".alert_events WHERE triggered_at >= date_trunc('month', NOW())")
        ).scalar() or 0
        try:
            firmware_releases = db.execute(
                text(f'SELECT COUNT(*) FROM "{schema}".firmware_releases WHERE is_active = TRUE')
            ).scalar() or 0
        except Exception:
            firmware_releases = 0

    data_points_this_month = _count_influxdb_points(tenant.influxdb_org_id, tenant.influxdb_token or "")

    return {
        "total_devices": total_devices,
        "online_devices": online_devices,
        "data_points_this_month": data_points_this_month,
        "alert_events_this_month": alert_events_this_month,
        "firmware_releases": firmware_releases,
    }


# ---------------------------------------------------------------------------
# ファームウェア管理
# ---------------------------------------------------------------------------

@router.get("/me/firmware")
def list_firmware(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        add_firmware_tables_to_tenant_schema(tenant_id)
        rows = db.execute(text(f'''
            SELECT id, version, target_model, file_size, checksum, description, is_active, uploaded_at
            FROM "{schema}".firmware_releases
            WHERE is_active = TRUE
            ORDER BY uploaded_at DESC
        ''')).fetchall()
    return [
        {
            "id": str(r.id),
            "version": r.version,
            "target_model": r.target_model,
            "file_size": r.file_size,
            "checksum": r.checksum,
            "description": r.description,
            "is_active": r.is_active,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in rows
    ]


@router.post("/me/firmware", status_code=status.HTTP_201_CREATED)
async def upload_firmware_release(
    version: str = Form(...),
    target_model: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    payload: dict = Depends(_require_admin_or_operator),
):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Firmware file too large (max 100 MB)")
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()
    firmware_id = str(uuid_lib.uuid4())
    add_firmware_tables_to_tenant_schema(tenant_id)
    minio_key = upload_firmware(tenant_id, firmware_id, content, file.content_type)
    with SessionLocal() as db:
        db.execute(text(f'''
            INSERT INTO "{schema}".firmware_releases
            (id, version, target_model, minio_key, file_size, checksum, description)
            VALUES (:id, :version, :target_model, :minio_key, :file_size, :checksum, :description)
        '''), {
            "id": firmware_id, "version": version, "target_model": target_model,
            "minio_key": minio_key, "file_size": len(content),
            "checksum": checksum, "description": description,
        })
        write_audit_log(db, "tenant", payload["sub"], payload["email"],
                        "upload_firmware", tenant_id=tenant_id,
                        resource_type="firmware", resource_id=version)
        db.commit()
    return {"id": firmware_id, "version": version, "checksum": checksum, "file_size": len(content)}


@router.delete("/me/firmware/{firmware_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_firmware(firmware_id: str, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        result = db.execute(text(f'''
            UPDATE "{schema}".firmware_releases SET is_active = FALSE WHERE id = :id
        '''), {"id": firmware_id})
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found")
        write_audit_log(db, "tenant", payload["sub"], payload["email"],
                        "delete_firmware", tenant_id=tenant_id,
                        resource_type="firmware", resource_id=firmware_id)
        db.commit()


class _OtaDispatchBody(BaseModel):
    firmware_id: str


@router.post("/me/devices/{device_id}/ota")
def dispatch_ota(device_id: str, body: _OtaDispatchBody, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    firmware_id = body.firmware_id
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', firmware_id.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid firmware_id")
    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT minio_key, version, checksum, file_size
            FROM "{schema}".firmware_releases
            WHERE id = :id AND is_active = TRUE
        '''), {"id": firmware_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found or inactive")
        token = create_firmware_download_token(firmware_id, tenant_id, row.minio_key)
        download_url = f"https://{settings.platform_domain}/api/firmware-download?token={token}"
        publish_ota_command(tenant_id, device_id, {
            "firmware_id": firmware_id,
            "version": row.version,
            "download_url": download_url,
            "checksum": row.checksum,
            "file_size": row.file_size,
        })
        db.execute(text(f'''
            INSERT INTO "{schema}".ota_events (firmware_id, device_id)
            VALUES (:firmware_id, :device_id)
        '''), {"firmware_id": firmware_id, "device_id": device_id})
        write_audit_log(db, "tenant", payload["sub"], payload["email"],
                        "ota_send", tenant_id=tenant_id,
                        resource_type="device", resource_id=device_id,
                        detail={"firmware_id": firmware_id, "version": row.version})
        db.commit()
    return {"status": "dispatched", "device_id": device_id, "firmware_id": firmware_id}


class _GroupOtaDispatchBody(BaseModel):
    firmware_id: str


@router.post("/me/groups/{group_id}/ota")
def dispatch_ota_to_group(group_id: str, body: _GroupOtaDispatchBody, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    group_id = _validate_uuid(group_id, "group_id")
    firmware_id = _validate_uuid(body.firmware_id, "firmware_id")
    try:
        device_ids = list_group_device_ids(schema, group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT minio_key, version, checksum, file_size
            FROM "{schema}".firmware_releases
            WHERE id = :id AND is_active = TRUE
        '''), {"id": firmware_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found or inactive")

        results = []
        for device_id in device_ids:
            try:
                token = create_firmware_download_token(firmware_id, tenant_id, row.minio_key)
                download_url = f"https://{settings.platform_domain}/api/firmware-download?token={token}"
                publish_ota_command(tenant_id, device_id, {
                    "firmware_id": firmware_id,
                    "version": row.version,
                    "download_url": download_url,
                    "checksum": row.checksum,
                    "file_size": row.file_size,
                })
                with db.begin_nested():
                    db.execute(text(f'''
                        INSERT INTO "{schema}".ota_events (firmware_id, device_id)
                        VALUES (:firmware_id, :device_id)
                    '''), {"firmware_id": firmware_id, "device_id": device_id})
                    write_audit_log(db, "tenant", payload["sub"], payload["email"],
                                    "ota_send", tenant_id=tenant_id,
                                    resource_type="device", resource_id=device_id,
                                    detail={"firmware_id": firmware_id, "version": row.version, "group_id": group_id})
                results.append({"device_id": device_id, "status": "dispatched"})
            except Exception as e:
                results.append({"device_id": device_id, "status": "failed", "error": str(e)})
        db.commit()

    return {"firmware_id": firmware_id, "group_id": group_id, "results": results}


# ---------------------------------------------------------------------------
# テナント削除（テナント管理者のみ）
# ---------------------------------------------------------------------------

@router.delete("/me/tenant", status_code=status.HTTP_202_ACCEPTED)
def delete_my_tenant(background_tasks: BackgroundTasks, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if tenant.status == "deleted":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already being deleted")
        influxdb_org_id = tenant.influxdb_org_id
        grafana_org_id = tenant.grafana_org_id
        tenant.status = "deleted"
        tenant.slug = f"{tenant.slug}_del{int(time.time())}"
        db.commit()
    background_tasks.add_task(teardown_tenant, tenant_id, influxdb_org_id, grafana_org_id)
    return {"status": "deletion_queued"}


# ---------------------------------------------------------------------------
# ダッシュボードパネル設定
# ---------------------------------------------------------------------------

class PanelType(str, Enum):
    timeseries    = "timeseries"
    barchart      = "barchart"
    histogram     = "histogram"
    heatmap       = "heatmap"
    state_timeline = "state-timeline"
    gauge         = "gauge"
    stat          = "stat"
    bargauge      = "bargauge"
    table         = "table"


class PanelConfigItem(BaseModel):
    sensor_key: str
    panel_type: PanelType

    @field_validator("sensor_key")
    @classmethod
    def validate_sensor_key(cls, v: str) -> str:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', v):
            raise ValueError("sensor_key must be alphanumeric, underscore, or hyphen (1-64 chars)")
        return v


@router.get("/dashboard/panel-configs")
def get_panel_configs(group_id: Optional[str] = Query(default=None), payload: dict = Depends(_require_tenant)):
    from app.models.public import DashboardPanelConfig
    tenant_id = payload["tenant_id"]
    if group_id is not None:
        group_id = _validate_uuid(group_id, "group_id")
    with SessionLocal() as db:
        rows = db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id,
            DashboardPanelConfig.group_id == group_id,
        ).all()
    return [{"sensor_key": r.sensor_key, "panel_type": r.panel_type} for r in rows]


@router.put("/dashboard/panel-configs", status_code=204)
def put_panel_configs(
    items: list[PanelConfigItem],
    group_id: Optional[str] = Query(default=None),
    payload: dict = Depends(_require_admin_or_operator),
):
    from app.models.public import DashboardPanelConfig, Tenant
    from app.services.grafana import sync_tenant_dashboard_with_configs
    tenant_id = payload["tenant_id"]

    if group_id is not None:
        group_id = _validate_uuid(group_id, "group_id")
        if not group_exists(_schema(tenant_id), group_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    # Validate: no duplicate sensor_keys
    sensor_keys = [item.sensor_key for item in items]
    if len(sensor_keys) != len(set(sensor_keys)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Duplicate sensor_key values")

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        # Capture values before commit to avoid DetachedInstanceError
        grafana_org_id = tenant.grafana_org_id
        tenant_name = tenant.name

        # 全件置き換え（テナント全体のデフォルト or 指定グループの設定のみ）
        db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id,
            DashboardPanelConfig.group_id == group_id,
        ).delete()
        for item in items:
            db.add(DashboardPanelConfig(
                tenant_id=tenant_id,
                group_id=group_id,
                sensor_key=item.sensor_key,
                panel_type=item.panel_type.value,
            ))
        db.commit()

    # グループ別設定は Grafana ダッシュボード同期の対象外（テナント全体のデフォルトのみ同期）
    if grafana_org_id and group_id is None:
        configs = [{"sensor_key": i.sensor_key, "panel_type": i.panel_type.value} for i in items]
        try:
            sync_tenant_dashboard_with_configs(int(grafana_org_id), tenant_name, configs)
        except Exception as e:
            print(f"[panel_configs] Grafana sync failed: {e}")


@router.get("/dashboard/sensor-keys")
def get_sensor_keys(payload: dict = Depends(_require_tenant)):
    """テナントのデバイスが持つ InfluxDB フィールドキー（センサーキー）一覧を返す。"""
    from app.services.grafana import get_tenant_sensor_keys
    tenant_id = payload["tenant_id"]
    with engine.connect() as conn:
        tenant_row = conn.execute(
            text("SELECT name, influxdb_org_id FROM tenants WHERE id = :tid"),
            {"tid": tenant_id},
        ).first()
    if not tenant_row or not tenant_row.influxdb_org_id:
        return []
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT device_name FROM "{schema}".devices WHERE device_name IS NOT NULL')
        ).fetchall()
    device_names = [r.device_name for r in rows if r.device_name]
    return get_tenant_sensor_keys(tenant_row.influxdb_org_id, device_names)


# ---------------------------------------------------------------------------
# 監査ログ
# ---------------------------------------------------------------------------

@router.get("/audit-logs", response_model=AuditLogListOut)
def get_tenant_audit_logs(
    action:  str | None = Query(default=None),
    from_dt: datetime | None = Query(default=None),
    to_dt:   datetime | None = Query(default=None),
    limit:   int = Query(default=50, ge=1, le=100),
    offset:  int = Query(default=0, ge=0),
    payload: dict = Depends(_require_admin),
):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        q = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
        if action:
            q = q.filter(AuditLog.action == action)
        if from_dt:
            q = q.filter(AuditLog.created_at >= from_dt)
        if to_dt:
            q = q.filter(AuditLog.created_at <= to_dt)
        total = q.count()
        rows = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
        items = [
            AuditLogOut(
                id=str(r.id),
                actor_type=r.actor_type,
                actor_email=r.actor_email,
                tenant_id=str(r.tenant_id) if r.tenant_id else None,
                action=r.action,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                detail=r.detail,
                ip_address=r.ip_address,
                result=r.result,
                created_at=r.created_at,
            )
            for r in rows
        ]
    return AuditLogListOut(total=total, items=items)


# ---------------------------------------------------------------------------
# デバイスグループ管理
# ---------------------------------------------------------------------------

@router.get("/me/groups", response_model=list[GroupOut])
def list_device_groups_portal(payload: dict = Depends(_require_tenant)):
    return list_groups(_schema(payload["tenant_id"]))


@router.post("/me/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_device_group_portal(body: GroupCreate, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    try:
        group = create_group(_schema(tenant_id), body.name, body.description)
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("tenant", payload["sub"], payload["email"], "create_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group["id"],
              detail={"name": group["name"]})
    return group


@router.patch("/me/groups/{group_id}", response_model=GroupOut)
def update_device_group_portal(group_id: str, body: GroupUpdate, payload: dict = Depends(_require_admin)):
    group_id = _validate_uuid(group_id, "group_id")
    tenant_id = payload["tenant_id"]
    try:
        group = update_group(_schema(tenant_id), group_id, body.name, body.description)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("tenant", payload["sub"], payload["email"], "update_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id,
              detail=body.model_dump(exclude_none=True))
    return group


@router.delete("/me/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device_group_portal(group_id: str, payload: dict = Depends(_require_admin)):
    group_id = _validate_uuid(group_id, "group_id")
    tenant_id = payload["tenant_id"]
    try:
        delete_group(_schema(tenant_id), group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupInUseError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "group_in_use", "alert_rules": e.rules})
    log_audit("tenant", payload["sub"], payload["email"], "delete_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id)
