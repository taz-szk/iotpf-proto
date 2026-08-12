from fastapi import FastAPI
from app.routers import health, auth, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth, tenant_users, tenant_devices, tenant_grafana, tenant_portal, public_access
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token

app = FastAPI(title="IoT Platform Core API", version="0.1.0")

@app.on_event("startup")
def on_startup():
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token):
        try:
            migrate()
        except Exception as e:
            print(f"Migration warning: {e}")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(provisioning.router)
app.include_router(emqx.router)
app.include_router(provisioning_tokens.router)
app.include_router(alert_rules.router)
app.include_router(emqx_events.router)
app.include_router(firmware.router)
app.include_router(stats.router)
app.include_router(tenant_auth.router)
app.include_router(tenant_users.router)
app.include_router(tenant_devices.router)
app.include_router(tenant_grafana.router)
app.include_router(tenant_portal.router)
app.include_router(public_access.router)
