import io
from datetime import datetime, timedelta, timezone
from typing import Iterator

from minio import Minio
from jose import jwt as jose_jwt

from app.config import settings


def _get_client() -> Minio:
    endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
    secure = settings.minio_endpoint.startswith("https://")
    return Minio(endpoint, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=secure)


def _ensure_bucket() -> None:
    client = _get_client()
    if not client.bucket_exists(settings.minio_firmware_bucket):
        client.make_bucket(settings.minio_firmware_bucket)


def upload_firmware(tenant_id: str, firmware_id: str, data: bytes, content_type: str) -> str:
    _ensure_bucket()
    client = _get_client()
    key = f"{tenant_id}/{firmware_id}"
    client.put_object(
        settings.minio_firmware_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type or "application/octet-stream",
    )
    return key


def stream_firmware(minio_key: str) -> Iterator[bytes]:
    client = _get_client()
    obj = client.get_object(settings.minio_firmware_bucket, minio_key)
    try:
        for chunk in obj.stream(amt=65536):
            yield chunk
    finally:
        obj.close()
        obj.release_conn()


def delete_firmware(minio_key: str) -> None:
    client = _get_client()
    client.remove_object(settings.minio_firmware_bucket, minio_key)


def create_firmware_download_token(firmware_id: str, tenant_id: str, minio_key: str) -> str:
    payload = {
        "firmware_id": firmware_id,
        "tenant_id": tenant_id,
        "minio_key": minio_key,
        "purpose": "firmware_download",
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=24),
    }
    return jose_jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_firmware_download_token(token: str) -> dict:
    try:
        payload = jose_jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except Exception as e:
        raise ValueError(f"Invalid token: {e}") from e
    if payload.get("purpose") != "firmware_download":
        raise ValueError("Token purpose mismatch")
    return payload
