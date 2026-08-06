import hashlib
import re
import uuid as uuid_lib
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from app.database import SessionLocal, add_firmware_tables_to_tenant_schema
from app.models.public import Tenant
from app.services.auth import verify_token
from app.services.emqx_publisher import publish_ota_command
from app.services.minio_client import (
    create_firmware_download_token,
    decode_firmware_download_token,
    delete_firmware,
    stream_firmware,
    upload_firmware,
)
from app.config import settings

router = APIRouter()
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _validate_uuid(value: str, field: str = "id") -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid {field}")
    return value.lower()


def _validate_tenant(tenant_id: str) -> tuple[str, str]:
    """Returns (tenant_name, schema_suffix). Raises 404 if not found."""
    _validate_uuid(tenant_id, "tenant_id")
    with SessionLocal() as db:
        t = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not t:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        return t.name, tenant_id.replace("-", "_")


@router.post("/tenants/{tenant_id}/firmware", status_code=status.HTTP_201_CREATED)
async def upload_firmware_release(
    tenant_id: str,
    version: str = Form(...),
    target_model: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    _: dict = Depends(_require_platform),
):
    tenant_name, schema_suffix = _validate_tenant(tenant_id)
    content = await file.read()
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()
    firmware_id = str(uuid_lib.uuid4())

    add_firmware_tables_to_tenant_schema(tenant_id)
    minio_key = upload_firmware(tenant_id, firmware_id, content, file.content_type)

    with SessionLocal() as db:
        schema = f"tenant_{schema_suffix}"
        db.execute(text(f'''
            INSERT INTO "{schema}".firmware_releases
            (id, version, target_model, minio_key, file_size, checksum, description)
            VALUES (:id, :version, :target_model, :minio_key, :file_size, :checksum, :description)
        '''), {
            "id": firmware_id,
            "version": version,
            "target_model": target_model,
            "minio_key": minio_key,
            "file_size": len(content),
            "checksum": checksum,
            "description": description,
        })
        db.commit()

    return {"id": firmware_id, "version": version, "checksum": checksum, "file_size": len(content)}


@router.get("/tenants/{tenant_id}/firmware")
def list_firmware(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_name, schema_suffix = _validate_tenant(tenant_id)
    schema = f"tenant_{schema_suffix}"
    with SessionLocal() as db:
        add_firmware_tables_to_tenant_schema(tenant_id)
        rows = db.execute(text(f'''
            SELECT id, version, target_model, file_size, checksum, description, is_active, uploaded_at
            FROM "{schema}".firmware_releases
            ORDER BY uploaded_at DESC
        ''')).fetchall()
        return [
            {
                "id": str(r.id),
                "version": r.version,
                "target_model": r.target_model,
                "file_size": r.file_size,
                "checksum": r.checksum,
                "description": r.description,
                "is_active": r.is_active,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            }
            for r in rows
        ]


@router.delete("/tenants/{tenant_id}/firmware/{firmware_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_firmware(tenant_id: str, firmware_id: str, _: dict = Depends(_require_platform)):
    _validate_uuid(firmware_id, "firmware_id")
    tenant_name, schema_suffix = _validate_tenant(tenant_id)
    schema = f"tenant_{schema_suffix}"
    with SessionLocal() as db:
        result = db.execute(text(f'''
            UPDATE "{schema}".firmware_releases
            SET is_active = FALSE
            WHERE id = :id
        '''), {"id": firmware_id})
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found")


@router.post("/tenants/{tenant_id}/devices/{device_id}/ota")
def dispatch_ota_command(
    tenant_id: str,
    device_id: str,
    body: dict,
    _: dict = Depends(_require_platform),
):
    _validate_uuid(tenant_id, "tenant_id")
    firmware_id = _validate_uuid(body.get("firmware_id", ""), "firmware_id")
    tenant_name, schema_suffix = _validate_tenant(tenant_id)
    schema = f"tenant_{schema_suffix}"

    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT minio_key, version, checksum, file_size
            FROM "{schema}".firmware_releases
            WHERE id = :id AND is_active = TRUE
        '''), {"id": firmware_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found or inactive")

        token = create_firmware_download_token(firmware_id, tenant_id, row.minio_key)
        download_url = f"https://{settings.platform_domain}/api/firmware-download?token={token}"

        publish_ota_command(tenant_id, device_id, {
            "firmware_id": firmware_id,
            "version": row.version,
            "download_url": download_url,
            "checksum": row.checksum,
            "file_size": row.file_size,
        })

        db.execute(text(f'''
            INSERT INTO "{schema}".ota_events (firmware_id, device_id)
            VALUES (:firmware_id, :device_id)
        '''), {"firmware_id": firmware_id, "device_id": device_id})
        db.commit()

    return {"status": "dispatched", "device_id": device_id, "firmware_id": firmware_id}


@router.get("/firmware-download")
def download_firmware(token: str):
    try:
        payload = decode_firmware_download_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    return StreamingResponse(
        stream_firmware(payload["minio_key"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="firmware-{payload["firmware_id"]}.bin"'},
    )
