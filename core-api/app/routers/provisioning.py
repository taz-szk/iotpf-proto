import logging
import uuid
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.backends import default_backend

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.models.public import ProvisioningToken
from app.schemas.device import ProvisionRequest, ProvisionOut
from app.services.provisioning import issue_device_cert_for_tenant

logger = logging.getLogger(__name__)
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
        expires_at = token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=timezone.utc)
        if expires_at.year < 2099 and expires_at < now:
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

        try:
            cert_pem, key_pem = issue_device_cert_for_tenant(tenant_id, req.device_id)
        except RuntimeError as exc:
            logger.error("Certificate issuance failed for %s:%s — %s", tenant_id, req.device_id, exc)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Certificate issuance failed")

        try:
            cert_obj = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())
            cert_not_after = cert_obj.not_valid_after_utc
        except Exception:
            cert_not_after = None

        device_name = req.device_name.strip() or req.device_id
        db.execute(
            text(f'''INSERT INTO "{schema}".devices
                         (id, device_id, device_name, provisioning_token_id, connection_status, fw_version, cert_not_after)
                     VALUES (:id, :did, :dname, :tok_id, 'offline', '1.0.0', :cert_not_after)'''),
            {"id": str(uuid.uuid4()), "did": req.device_id, "dname": device_name,
             "tok_id": str(token.id), "cert_not_after": cert_not_after}
        )
        token.registered_count += 1
        db.commit()

    try:
        with open(settings.step_ca_root) as f:
            ca_cert = f.read()
    except OSError:
        ca_cert = ""

    return ProvisionOut(
        tenant_id=tenant_id,
        device_id=req.device_id,
        certificate=cert_pem,
        private_key=key_pem,
        ca_certificate=ca_cert,
    )
