import logging
import threading

from fastapi import FastAPI
from app.routers import health, auth, mfa, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth, tenant_mfa, tenant_users, tenant_devices, tenant_grafana, tenant_portal, public_access, platform
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs
from app.services.emqx_setup import ensure_emqx_rules
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="IoT Platform Core API", version="0.1.0")

def _run_emqx_setup():
    try:
        ensure_emqx_rules(
            settings.emqx_api_url, settings.emqx_api_user, settings.emqx_api_password,
            settings.emqx_webhook_secret,
        )
    except Exception as exc:
        logger.error("EMQX setup thread crashed: %s", exc, exc_info=True)

@app.on_event("startup")
def on_startup():
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs):
        try:
            migrate()
        except Exception as e:
            print(f"Migration warning: {e}")
    # EMQX 接続イベントルールを保証 (バックグラウンドで実行)
    threading.Thread(target=_run_emqx_setup, daemon=True).start()

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(mfa.router)
app.include_router(tenants.router)
app.include_router(provisioning.router)
app.include_router(emqx.router)
app.include_router(provisioning_tokens.router)
app.include_router(alert_rules.router)
app.include_router(emqx_events.router)
app.include_router(firmware.router)
app.include_router(stats.router)
app.include_router(tenant_auth.router)
app.include_router(tenant_mfa.router)
app.include_router(platform.router)
app.include_router(tenant_users.router)
app.include_router(tenant_devices.router)
app.include_router(tenant_grafana.router)
app.include_router(tenant_portal.router)
app.include_router(public_access.router)
