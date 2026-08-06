from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from app.services.auth import verify_token
from app.database import SessionLocal
from sqlalchemy import text
import uuid, re

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

class AlertRuleCreate(BaseModel):
    device_id: Optional[str] = None
    sensor_key: str
    condition: str
    threshold: Optional[float] = None
    trigger_mode: str = "consecutive"
    consecutive_count: int = 3
    duration_sec: int = 60
    severity: str = "warning"
    notify_emails: list[str] = []

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
    emails = "{" + ",".join(f'"{e}"' for e in body.notify_emails) + "}"
    with SessionLocal() as db:
        db.execute(text(f'''
            INSERT INTO "{schema}".alert_rules
              (id, device_id, sensor_key, condition, threshold, trigger_mode,
               consecutive_count, duration_sec, severity, notify_emails)
            VALUES (:id, :did, :sk, :cond, :thr, :tm, :cc, :ds, :sev, :emails::TEXT[])
        '''), {
            "id": rule_id, "did": body.device_id, "sk": body.sensor_key,
            "cond": body.condition, "thr": body.threshold, "tm": body.trigger_mode,
            "cc": body.consecutive_count, "ds": body.duration_sec,
            "sev": body.severity, "emails": emails,
        })
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
