from unittest.mock import patch, MagicMock
import pytest
from app.services.minio_client import (
    upload_firmware,
    create_firmware_download_token,
    decode_firmware_download_token,
)

def test_upload_firmware_returns_key():
    with patch("app.services.minio_client.Minio") as MockMinio:
        mock_client = MagicMock()
        MockMinio.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        key = upload_firmware("tenant-abc", "fw-001", b"binary_data", "application/octet-stream")

    assert key == "tenant-abc/fw-001"
    mock_client.put_object.assert_called_once()

def test_download_token_roundtrip():
    tenant_id = "11111111-1111-1111-1111-111111111111"
    firmware_id = "22222222-2222-2222-2222-222222222222"
    minio_key = f"{tenant_id}/{firmware_id}"
    token = create_firmware_download_token(firmware_id, tenant_id, minio_key)
    payload = decode_firmware_download_token(token)
    assert payload["firmware_id"] == firmware_id
    assert payload["tenant_id"] == tenant_id
    assert payload["minio_key"] == minio_key

def test_decode_firmware_token_invalid_raises():
    with pytest.raises(ValueError):
        decode_firmware_download_token("not.a.valid.token")

def test_decode_firmware_token_wrong_purpose_raises():
    import jwt
    from app.config import settings
    from datetime import datetime, timedelta, timezone
    bad_token = jwt.encode(
        {"purpose": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ValueError, match="purpose"):
        decode_firmware_download_token(bad_token)


def test_decode_firmware_token_invalid_minio_key_raises():
    import jwt
    from app.config import settings
    from datetime import datetime, timedelta, timezone
    bad_token = jwt.encode(
        {
            "purpose": "firmware_download",
            "minio_key": "../../etc/passwd",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ValueError, match="minio_key"):
        decode_firmware_download_token(bad_token)
