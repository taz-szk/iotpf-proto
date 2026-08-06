from fastapi import FastAPI
from app.routers import health, auth, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events

app = FastAPI(title="IoT Platform Core API", version="0.1.0")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(provisioning.router)
app.include_router(emqx.router)
app.include_router(provisioning_tokens.router)
app.include_router(alert_rules.router)
app.include_router(emqx_events.router)
