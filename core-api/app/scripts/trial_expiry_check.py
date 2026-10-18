"""トライアル(セルフサインアップ)テナントの期限を日次でチェックする。
cronから `docker compose exec -T core-api python -m app.scripts.trial_expiry_check` で起動する。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app.models.public import Tenant, TenantSignupRequest
from app.services.mailer import send_trial_expiry_warning_email
from app.services.tenant import teardown_tenant

_WARNING_WINDOW = timedelta(days=7)


def _send_warning(db, tenant, now) -> bool:
    """警告メールを送信し、成功した場合のみtrial_warning_sent_atを立ててcommitする。
    宛先が見つからない場合は送信試行せず失敗として返す(フラグを立てず次回再試行)。"""
    with SessionLocal() as admin_db:
        schema = f"tenant_{str(tenant.id).replace('-', '_')}"
        admin_email = admin_db.execute(
            text(f'SELECT email FROM "{schema}".users WHERE role = \'admin\' ORDER BY created_at LIMIT 1')
        ).scalar()
    if not admin_email:
        return False
    expires_at = tenant.trial_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    sent = send_trial_expiry_warning_email(admin_email, tenant.name, expires_at.date().isoformat())
    if sent:
        tenant.trial_warning_sent_at = now
        db.commit()
    return sent


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

            if expires_at <= now:
                # 一度も警告を送れていない(cron停止等)場合はいきなり削除せず、
                # まず警告を送って次サイクル以降の削除に回す(無警告での唐突な削除を防ぐ)
                if tenant.trial_warning_sent_at is None:
                    if _send_warning(db, tenant, now):
                        warned += 1
                    continue

                # 延長申請が警告ウィンドウ内(直近)であれば管理者レビュー待ちとして削除をスキップ。
                # それより古い(放置された)申請は「無視すれば次サイクルで削除」の対象とする。
                ext_requested_at = tenant.trial_extension_requested_at
                if ext_requested_at is not None:
                    if ext_requested_at.tzinfo is None:
                        ext_requested_at = ext_requested_at.replace(tzinfo=timezone.utc)
                    if (now - ext_requested_at) <= _WARNING_WINDOW:
                        continue

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
                if _send_warning(db, tenant, now):
                    warned += 1

        # 未確認(期限切れ)のサインアップ仮登録行を掃除する。スラッグを無期限に予約させない。
        db.query(TenantSignupRequest).filter(TenantSignupRequest.expires_at < now).delete()
        db.commit()

    return {"warned": warned, "deleted": deleted}


if __name__ == "__main__":
    result = run_trial_expiry_check()
    print(f"trial_expiry_check: warned={result['warned']} deleted={result['deleted']}")
