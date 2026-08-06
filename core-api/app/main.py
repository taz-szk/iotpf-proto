from fastapi import FastAPI
from app.routers import health, auth

app = FastAPI(title="IoT Platform Core API", version="0.1.0")
app.include_router(health.router)
app.include_router(auth.router)
