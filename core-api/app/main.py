from fastapi import FastAPI
from app.routers import health, auth, tenants, provisioning

app = FastAPI(title="IoT Platform Core API", version="0.1.0")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(provisioning.router)
