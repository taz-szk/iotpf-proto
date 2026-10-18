"""トライアル(セルフサインアップ)テナントの期限を日次でチェックする。
cronから `docker compose exec -T core-api python -m app.scripts.trial_expiry_check` で起動する。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app.models.public import Tenant
from app.services.mailer import send_trial_expiry_warning_email
from app.services.tenant import teardown_tenant

_WARNING_WINDOW = timedelta(days=7)


def run_trial_expiry_check() -> dict:
    now = datetime.now(timezone.utc)
    warned = 0
    deleted = 0

    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(
            Tenant.signup_source == "self_signup", Tenant.status == "active",
        ).all()

        for tenant in tenants:
            if tenant.status != "active":
                continue
            expires_at = tenant.trial_expires_at
            if expires_at is None:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at <= now and tenant.trial_extension_requested_at is None:
                tenant.status = "deleted"
                tenant.slug = f"{tenant.slug}_del{int(now.timestamp())}"
                influxdb_org_id = tenant.influxdb_org_id
                grafana_org_id = tenant.grafana_org_id
                tenant_id = str(tenant.id)
                db.commit()
                teardown_tenant(tenant_id, influxdb_org_id, grafana_org_id)
                deleted += 1
                continue

            if (expires_at - now) <= _WARNING_WINDOW and tenant.trial_warning_sent_at is None:
                with SessionLocal() as admin_db:
                    schema = f"tenant_{str(tenant.id).replace('-', '_')}"
                    admin_email = admin_db.execute(
                        text(f'SELECT email FROM "{schema}".users WHERE role = \'admin\' ORDER BY created_at LIMIT 1')
                    ).scalar()
                if admin_email:
                    send_trial_expiry_warning_email(
                        admin_email, tenant.name, expires_at.date().isoformat(),
                    )
                tenant.trial_warning_sent_at = now
                db.commit()
                warned += 1

    return {"warned": warned, "deleted": deleted}


if __name__ == "__main__":
    result = run_trial_expiry_check()
    print(f"trial_expiry_check: warned={result['warned']} deleted={result['deleted']}")
