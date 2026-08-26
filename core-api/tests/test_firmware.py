import uuid
from unittest.mock import patch, MagicMock
import io

def _make_jwt_payload():
    return {"sub": str(uuid.uuid4()), "type": "platform"}

def test_upload_firmware_endpoint_returns_201(client):
    tenant_id = str(uuid.uuid4())
    firmware_id = str(uuid.uuid4())

    with patch("app.routers.firmware.verify_token", return_value=_make_jwt_payload()), \
         patch("app.routers.firmware._validate_tenant", return_value=("test-tenant", tenant_id.replace("-", "_"))), \
         patch("app.routers.firmware.upload_firmware", return_value=f"{tenant_id}/{firmware_id}") as mock_upload, \
         patch("app.routers.firmware.add_firmware_tables_to_tenant_schema"), \
         patch("app.routers.firmware.SessionLocal") as mock_session:

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db

        resp = client.post(
            f"/tenants/{tenant_id}/firmware",
            files={"file": ("firmware.bin", b"\x00\x01\x02", "application/octet-stream")},
            data={"version": "1.0.0", "description": "test release"},
            headers={"Authorization": "Bearer dummy"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["version"] == "1.0.0"
    assert "checksum" in data

def test_dispatch_ota_command_returns_200(client):
    tenant_id = str(uuid.uuid4())
    device_id = "device-001"
    firmware_id = str(uuid.uuid4())

    with patch("app.routers.firmware.verify_token", return_value=_make_jwt_payload()), \
         patch("app.routers.firmware._validate_tenant", return_value=("test-tenant", tenant_id.replace("-", "_"))), \
         patch("app.routers.firmware.publish_ota_command") as mock_publish, \
         patch("app.routers.firmware.create_firmware_download_token", return_value="tok"), \
         patch("app.routers.firmware.SessionLocal") as mock_session:

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_row = MagicMock()
        mock_row.minio_key = f"{tenant_id}/{firmware_id}"
        mock_row.version = "1.0.0"
        mock_row.checksum = "sha256:abc"
        mock_row.file_size = 1024
        mock_db.execute.return_value.fetchone.return_value = mock_row

        resp = client.post(
            f"/tenants/{tenant_id}/devices/{device_id}/ota",
            json={"firmware_id": firmware_id},
            headers={"Authorization": "Bearer dummy"},
        )

    assert resp.status_code == 200
    mock_publish.assert_called_once()

def test_firmware_download_with_valid_token(client):
    from app.services.minio_client import create_firmware_download_token
    tenant_id = str(uuid.uuid4())
    firmware_id = str(uuid.uuid4())
    minio_key = f"{tenant_id}/{firmware_id}"
    token = create_firmware_download_token(firmware_id, tenant_id, minio_key)

    with patch("app.routers.firmware.stream_firmware", return_value=iter([b"chunk1", b"chunk2"])):
        resp = client.get(f"/firmware-download?token={token}")

    assert resp.status_code == 200
    assert resp.content == b"chunk1chunk2"

def test_firmware_download_with_invalid_token(client):
    resp = client.get("/firmware-download?token=bad.token.here")
    assert resp.status_code == 403
