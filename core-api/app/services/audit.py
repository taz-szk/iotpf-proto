import threading
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.public import AuditLog


def write_audit_log(
    db: Session,
    actor_type: str,
    actor_id: str,
    actor_email: str,
    action: str,
    *,
    tenant_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    result: str = "success",
) -> None:
    """監査ログを現在の DB セッションに追加する。commit は呼び出し側が行う。"""
    db.add(AuditLog(
        id=_uuid.uuid4(),
        actor_type=actor_type,
        actor_id=_uuid.UUID(actor_id),
        actor_email=actor_email,
        tenant_id=_uuid.UUID(tenant_id) if tenant_id else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=ip_address,
        result=result,
    ))


def log_audit(
    actor_type: str,
    actor_id: str,
    actor_email: str,
    action: str,
    **kwargs,
) -> None:
    """セッションがないパス用。内部で Session を作りcommit・例外をすべて吸収する。"""
    try:
        with SessionLocal() as db:
            write_audit_log(db, actor_type, actor_id, actor_email, action, **kwargs)
            db.commit()
    except Exception:
        pass


def _purge_old_audit_logs() -> None:
    from app.config import settings
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.audit_log_retention_days)
    try:
        with SessionLocal() as db:
            db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()
            db.commit()
    except Exception:
        pass


def start_audit_purge_worker() -> None:
    """毎日 1 回古い監査ログを削除するバックグラウンドスレッドを起動する。"""
    def _loop() -> None:
        while True:
            _purge_old_audit_logs()
            time.sleep(86400)
    threading.Thread(target=_loop, daemon=True).start()
