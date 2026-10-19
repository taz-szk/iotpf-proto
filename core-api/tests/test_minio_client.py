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

def test_upload_export_file_uses_unique_key_per_generation():
    from app.services.minio_client import upload_export_file
    with patch("app.services.minio_client.Minio") as MockMinio:
        mock_client = MagicMock()
        MockMinio.return_value = mock_client
        mock_client.bucket_exists.return_value = True
        k1 = upload_export_file("tenant-abc", "job-1", b"a")
        k2 = upload_export_file("tenant-abc", "job-1", b"b")
    assert k1.startswith("tenant-abc/job-1/") and k2.startswith("tenant-abc/job-1/")
    assert k1 != k2  # 再生成で前回のファイルを上書きしない


def test_delete_all_tenant_exports_removes_only_tenant_prefix_objects():
    from app.config import settings
    from app.services.minio_client import delete_all_tenant_exports
    with patch("app.services.minio_client.Minio") as MockMinio:
        mock_client = MagicMock()
        MockMinio.return_value = mock_client
        mock_client.bucket_exists.return_value = True
        mock_client.list_objects.return_value = [MagicMock(object_name="tenant-abc/job-1/x"),
                                                 MagicMock(object_name="tenant-abc/job-2/y")]
        delete_all_tenant_exports("tenant-abc")
    mock_client.list_objects.assert_called_once_with(settings.minio_export_bucket, prefix="tenant-abc/", recursive=True)
    assert [c.args for c in mock_client.remove_object.call_args_list] == [
        (settings.minio_export_bucket, "tenant-abc/job-1/x"),
        (settings.minio_export_bucket, "tenant-abc/job-2/y"),
    ]


def test_delete_all_tenant_exports_noop_when_bucket_missing():
    from app.services.minio_client import delete_all_tenant_exports
    with patch("app.services.minio_client.Minio") as MockMinio:
        mock_client = MagicMock()
        MockMinio.return_value = mock_client
        mock_client.bucket_exists.return_value = False
        delete_all_tenant_exports("tenant-abc")
    mock_client.list_objects.assert_not_called()


def test_teardown_tenant_deletes_export_files():
    from app.services.tenant import teardown_tenant
    with patch("app.services.minio_client.delete_all_tenant_firmware") as mock_fw, \
         patch("app.services.minio_client.delete_all_tenant_exports") as mock_exports, \
         patch("app.services.tenant.engine"), \
         patch("app.services.tenant.SessionLocal"), \
         patch("app.services.tenant.httpx"), \
         patch("app.services.tenant.get_or_create_platform_org"), \
         patch("app.services.tenant.remove_tenant_datasource_from_platform_org"):
        teardown_tenant("tenant-abc", None, None)
    mock_fw.assert_called_once_with("tenant-abc")
    mock_exports.assert_called_once_with("tenant-abc")


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
