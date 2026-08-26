import hmac
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Any, Optional
from datetime import datetime
from app.tenant_cache import get_tenant_influx_config, get_device_name
from app.influx_writer import write_telemetry, write_device_status
from app.device_updater import update_last_seen
from app.config import settings

app = FastAPI(title="IoT Ingestion Service", version="0.1.0")


def _verify_emqx_secret(x_api_key: Optional[str] = Header(default=None)) -> None:
    if x_api_key is None or not hmac.compare_digest(
        x_api_key.encode(), settings.emqx_webhook_secret.encode()
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


class IngestRequest(BaseModel):
    tenant_id: str
    device_id: str
    payload: dict[str, Any]
    topic_type: str = "telemetry"
    timestamp: Optional[datetime] = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest", dependencies=[Depends(_verify_emqx_secret)])
def ingest(req: IngestRequest):
    config = get_tenant_influx_config(req.tenant_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {req.tenant_id}")

    if req.topic_type == "status":
        status = req.payload.get("status", "online")
        fw_version = req.payload.get("fw_version") or None
        update_last_seen(req.tenant_id, req.device_id, status, fw_version)
        device_name = get_device_name(req.tenant_id, req.device_id)
        write_device_status(
            org_id=config["org_id"],
            token=config["token"],
            tenant_id=req.tenant_id,
            device_id=req.device_id,
            device_name=device_name,
            status=status,
        )
        return {"result": "status_updated"}

    measurements = {k: v for k, v in req.payload.items()
                    if isinstance(v, (int, float)) and k != "timestamp"}
    if not measurements:
        return {"result": "no_numeric_fields"}

    device_name = get_device_name(req.tenant_id, req.device_id)
    write_telemetry(
        org_id=config["org_id"],
        token=config["token"],
        tenant_id=req.tenant_id,
        device_id=req.device_id,
        device_name=device_name,
        measurements=measurements,
        timestamp=req.timestamp,
    )
    update_last_seen(req.tenant_id, req.device_id, "online")
    return {"result": "written", "fields": list(measurements.keys())}
