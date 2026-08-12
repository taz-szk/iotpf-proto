from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, Literal
from app.services.auth import verify_token
from app.database import SessionLocal
from app.models.public import Tenant
from app.config import settings
from sqlalchemy import text, bindparam, ARRAY, String as SaString
import uuid, re, httpx

router = APIRouter(prefix="/tenants")
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

def _fetch_sensor_keys(influxdb_org_id: str) -> list[str]:
    query = (
        'import "influxdata/influxdb/schema"\n'
        'schema.fieldKeys(\n'
        '  bucket: "telemetry",\n'
        '  predicate: (r) => r._measurement == "telemetry",\n'
        '  start: -30d\n'
        ')'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={influxdb_org_id}",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
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


@router.get("/{tenant_id}/sensor-keys", response_model=list[str])
def list_sensor_keys(tenant_id: str, _: dict = Depends(_require_platform)):
    _schema(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant or not tenant.influxdb_org_id:
            return []
    return _fetch_sensor_keys(tenant.influxdb_org_id)


class AlertRuleCreate(BaseModel):
    device_id: Optional[str] = None
    sensor_key: str
    condition: Literal["above", "below", "equal", "device_offline"]
    threshold: Optional[float] = None
    trigger_mode: Literal["consecutive", "duration", "consecutive_and_duration"] = "consecutive"
    consecutive_count: int = 3
    duration_sec: int = 60
    severity: Literal["info", "warning", "critical"] = "warning"
    notify_emails: list[str] = []

class AlertRuleUpdate(BaseModel):
    device_id: Optional[str] = None
    sensor_key: Optional[str] = None
    condition: Optional[Literal["above", "below", "equal", "device_offline"]] = None
    threshold: Optional[float] = None
    trigger_mode: Optional[Literal["consecutive", "duration", "consecutive_and_duration"]] = None
    consecutive_count: Optional[int] = None
    duration_sec: Optional[int] = None
    severity: Optional[Literal["info", "warning", "critical"]] = None
    notify_emails: Optional[list[str]] = None

class AlertRuleOut(BaseModel):
    id: str
    device_id: Optional[str]
    sensor_key: str
    condition: str
    threshold: Optional[float]
    trigger_mode: str
    consecutive_count: int
    duration_sec: int
    severity: str
    notify_emails: list[str]
    is_active: bool

@router.post("/{tenant_id}/alert-rules", response_model=AlertRuleOut, status_code=201)
def create_alert_rule(tenant_id: str, body: AlertRuleCreate, _: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    rule_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.execute(
            text(f'''
                INSERT INTO "{schema}".alert_rules
                  (id, device_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails)
                VALUES (:id, :did, :sk, :cond, :thr, :tm, :cc, :ds, :sev, :emails)
            ''').bindparams(bindparam("emails", type_=ARRAY(SaString))),
            {
                "id": rule_id, "did": body.device_id, "sk": body.sensor_key,
                "cond": body.condition, "thr": body.threshold, "tm": body.trigger_mode,
                "cc": body.consecutive_count, "ds": body.duration_sec,
                "sev": body.severity, "emails": list(body.notify_emails),
            }
        )
        db.commit()
    return AlertRuleOut(
        id=rule_id, device_id=body.device_id, sensor_key=body.sensor_key,
        condition=body.condition, threshold=body.threshold,
        trigger_mode=body.trigger_mode, consecutive_count=body.consecutive_count,
        duration_sec=body.duration_sec, severity=body.severity,
        notify_emails=body.notify_emails, is_active=True,
    )

@router.get("/{tenant_id}/alert-rules", response_model=list[AlertRuleOut])
def list_alert_rules(tenant_id: str, _: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        rows = db.execute(text(f'''
            SELECT id, device_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE is_active = TRUE
        ''')).fetchall()
    return [AlertRuleOut(
        id=str(r.id), device_id=r.device_id, sensor_key=r.sensor_key,
        condition=r.condition, threshold=float(r.threshold) if r.threshold is not None else None,
        trigger_mode=r.trigger_mode, consecutive_count=r.consecutive_count,
        duration_sec=r.duration_sec, severity=r.severity,
        notify_emails=list(r.notify_emails) if r.notify_emails else [],
        is_active=r.is_active,
    ) for r in rows]

@router.patch("/{tenant_id}/alert-rules/{rule_id}", response_model=AlertRuleOut)
def update_alert_rule(tenant_id: str, rule_id: str, body: AlertRuleUpdate, _: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT id, device_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE id = :rid AND is_active = TRUE
        '''), {"rid": rule_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Rule not found")
        set_clauses = ", ".join(
            f"{col} = :{col}" for col in updates if col != "notify_emails"
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
            SELECT id, device_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE id = :rid
        '''), {"rid": rule_id}).fetchone()
    return AlertRuleOut(
        id=str(updated.id), device_id=updated.device_id, sensor_key=updated.sensor_key,
        condition=updated.condition, threshold=float(updated.threshold) if updated.threshold is not None else None,
        trigger_mode=updated.trigger_mode, consecutive_count=updated.consecutive_count,
        duration_sec=updated.duration_sec, severity=updated.severity,
        notify_emails=list(updated.notify_emails) if updated.notify_emails else [],
        is_active=updated.is_active,
    )

@router.delete("/{tenant_id}/alert-rules/{rule_id}", status_code=204)
def delete_alert_rule(tenant_id: str, rule_id: str, _: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        result = db.execute(text(f'''
            UPDATE "{schema}".alert_rules SET is_active = FALSE
            WHERE id = :rid AND is_active = TRUE
        '''), {"rid": rule_id})
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Rule not found")
    return
