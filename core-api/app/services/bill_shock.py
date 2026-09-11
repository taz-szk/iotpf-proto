from datetime import datetime, timezone

from sqlalchemy import text

from app.database import engine
from app.models.billing import BillingInvoice
from app.models.public import PlatformUser
from app.services.audit import write_audit_log
from app.services.billing import get_effective_bill_shock_threshold
from app.services.mailer import send_bill_shock_email

SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000000"
SYSTEM_ACTOR_EMAIL = "system@platform"


def _get_tenant_admin_emails(schema: str) -> list[str]:
    """そのテナントのrole='admin'かつ有効なユーザーのメールアドレス一覧を返す。"""
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT email FROM "{schema}".users WHERE role = \'admin\' AND is_active')
        ).fetchall()
    return [r.email for r in rows]


def _get_platform_admin_emails(db) -> list[str]:
    """有効な全PF管理者のメールアドレス一覧を返す。"""
    rows = db.query(PlatformUser).filter(PlatformUser.is_active == True).all()  # noqa: E712
    return [r.email for r in rows]


def check_and_notify_bill_shock(db, tenant, schema: str, invoice: BillingInvoice) -> None:
    """当月draft請求書の合計金額がしきい値を超過していれば、監査ログ記録＋メール送信で
    通知する。しきい値未設定・未超過・今月既通知のいずれかならno-op。"""
    threshold = get_effective_bill_shock_threshold(db, tenant)
    if threshold is None:
        return
    if invoice.total_amount <= threshold:
        return
    if invoice.bill_shock_notified_at is not None:
        return

    write_audit_log(
        db, "system", SYSTEM_ACTOR_ID, SYSTEM_ACTOR_EMAIL, "bill_shock_threshold_exceeded",
        tenant_id=str(tenant.id), resource_type="billing_invoice",
        detail={
            "target_year_month": invoice.target_year_month,
            "total_amount": invoice.total_amount,
            "threshold_amount": threshold,
        },
    )

    to_emails = _get_tenant_admin_emails(schema) + _get_platform_admin_emails(db)
    send_bill_shock_email(
        to_emails=to_emails, tenant_name=tenant.name,
        target_year_month=invoice.target_year_month,
        total_amount=invoice.total_amount, threshold_amount=threshold,
    )

    invoice.bill_shock_notified_at = datetime.now(timezone.utc)
    db.commit()
