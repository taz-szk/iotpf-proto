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
    token = create_firmware_download_token("fw-001", "tenant-abc", "tenant-abc/fw-001")
    payload = decode_firmware_download_token(token)
    assert payload["firmware_id"] == "fw-001"
    assert payload["tenant_id"] == "tenant-abc"
    assert payload["minio_key"] == "tenant-abc/fw-001"

def test_decode_firmware_token_invalid_raises():
    with pytest.raises(ValueError):
        decode_firmware_download_token("not.a.valid.token")

def test_decode_firmware_token_wrong_purpose_raises():
    from jose import jwt as jose_jwt
    from app.config import settings
    from datetime import datetime, timedelta
    bad_token = jose_jwt.encode(
        {"purpose": "access", "exp": datetime.utcnow() + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ValueError, match="purpose"):
        decode_firmware_download_token(bad_token)
