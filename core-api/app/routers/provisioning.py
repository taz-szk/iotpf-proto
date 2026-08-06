from fastapi import APIRouter, HTTPException, status
from datetime import datetime, timezone
from app.schemas.device import ProvisionRequest, ProvisionOut
from app.models.public import ProvisioningToken
from app.database import SessionLocal
from app.services.provisioning import issue_device_cert_for_tenant
from sqlalchemy import text
import uuid

router = APIRouter()

@router.post("/provision", response_model=ProvisionOut)
def provision(req: ProvisionRequest):
    with SessionLocal() as db:
        token = db.query(ProvisioningToken).filter(
            ProvisioningToken.token == req.bootstrap_token,
            ProvisioningToken.is_active == True,
        ).first()

        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        now = datetime.now(timezone.utc)
        if token.expires_at.replace(tzinfo=timezone.utc) < now:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

        if token.registered_count >= token.max_devices:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device limit reached")

        tenant_id = str(token.tenant_id)
        schema = f"tenant_{tenant_id.replace('-', '_')}"

        existing = db.execute(
            text(f'SELECT id FROM "{schema}".devices WHERE device_id = :did'),
            {"did": req.device_id}
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device already registered")

        cert_pem, key_pem = issue_device_cert_for_tenant(tenant_id, req.device_id)

        db.execute(
            text(f'''INSERT INTO "{schema}".devices (id, device_id, connection_status)
                     VALUES (:id, :did, 'offline')'''),
            {"id": str(uuid.uuid4()), "did": req.device_id}
        )
        token.registered_count += 1
        db.commit()

    return ProvisionOut(
        tenant_id=tenant_id,
        device_id=req.device_id,
        certificate=cert_pem,
        private_key=key_pem,
    )
