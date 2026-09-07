from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func

from app.database import SessionLocal
from app.models.public import AuditLog, Tenant
from app.schemas.audit import AuditLogListOut, AuditLogOut
from app.services.auth import verify_token

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


@router.get("", response_model=AuditLogListOut)
def list_audit_logs(
    tenant_id: str | None = Query(default=None),
    action:    str | None = Query(default=None),
    from_dt:   datetime | None = Query(default=None),
    to_dt:     datetime | None = Query(default=None),
    limit:     int = Query(default=50, ge=1, le=100),
    offset:    int = Query(default=0, ge=0),
    _: dict = Depends(_require_platform),
):
    with SessionLocal() as db:
        q = db.query(AuditLog)
        if tenant_id:
            q = q.filter(AuditLog.tenant_id == tenant_id)
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
