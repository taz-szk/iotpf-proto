"""ファームウェアのアップロードを、全量をメモリに読み込まずにチャンクで処理することのテスト。"""
import hashlib
import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services import firmware_upload
from app.services.firmware_upload import measure_upload


class _RecordingFile(io.BytesIO):
    """read()に渡されたサイズを記録する。サイズ指定なしの全量読み込みを検出するため。"""
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def test_measure_matches_sha256_and_size_and_rewinds():
    data = b"firmware-bytes-" * 200_000  # 約3MB(複数チャンクにまたがる)
    f = _RecordingFile(data)
    size, checksum = measure_upload(f)
    assert size == len(data)
    assert checksum == "sha256:" + hashlib.sha256(data).hexdigest()
    assert f.tell() == 0  # 続けてMinIOへ流せるよう先頭に戻す


def test_measure_never_reads_the_whole_file_at_once():
    f = _RecordingFile(b"x" * (firmware_upload.CHUNK_SIZE * 3 + 5))
    measure_upload(f)
    assert all(0 < s <= firmware_upload.CHUNK_SIZE for s in f.read_sizes)


def test_measure_rejects_oversize_and_stops_reading_early(monkeypatch):
    monkeypatch.setattr(firmware_upload, "MAX_FIRMWARE_BYTES", 2 * firmware_upload.CHUNK_SIZE)
    f = _RecordingFile(b"x" * (firmware_upload.CHUNK_SIZE * 50))
    with pytest.raises(HTTPException) as e:
        measure_upload(f)
    assert e.value.status_code == 413
    assert len(f.read_sizes) <= 4  # 上限を超えた時点で打ち切る(残りを読まない)


def test_measure_accepts_exactly_the_limit(monkeypatch):
    monkeypatch.setattr(firmware_upload, "MAX_FIRMWARE_BYTES", 1000)
    size, _ = measure_upload(io.BytesIO(b"x" * 1000))
    assert size == 1000
    with pytest.raises(HTTPException):
        measure_upload(io.BytesIO(b"x" * 1001))


def test_minio_upload_streams_a_file_object_with_its_length():
    from app.services.minio_client import upload_firmware
    with patch("app.services.minio_client.Minio") as MockMinio:
        client = MagicMock()
        MockMinio.return_value = client
        client.bucket_exists.return_value = True
        f = io.BytesIO(b"abcdef")
        key = upload_firmware("t", "fw", f, "application/octet-stream", length=6)
    assert key == "t/fw"
    args, kwargs = client.put_object.call_args
    assert args[2] is f and kwargs["length"] == 6


def _upload(client, path, body=b"\x00\x01\x02\x03"):
    return client.post(path, files={"file": ("fw.bin", body, "application/octet-stream")},
                       data={"version": "1.0.0"})


def test_platform_endpoint_passes_a_stream_not_bytes_to_minio(client):
    tenant_id = str(uuid.uuid4())
    with patch("app.routers.firmware.verify_token",
               return_value={"sub": str(uuid.uuid4()), "email": "a@b.c", "type": "platform"}), \
         patch("app.routers.firmware._validate_tenant", return_value=("t", tenant_id.replace("-", "_"))), \
         patch("app.routers.firmware.upload_firmware", return_value="k") as up, \
         patch("app.routers.firmware.add_firmware_tables_to_tenant_schema"), \
         patch("app.routers.firmware.SessionLocal"):
        resp = client.post(f"/tenants/{tenant_id}/firmware", headers={"Authorization": "Bearer x"},
                           files={"file": ("fw.bin", b"\x00\x01\x02\x03", "application/octet-stream")},
                           data={"version": "1.0.0"})
    assert resp.status_code == 201
    assert resp.json()["file_size"] == 4
    assert resp.json()["checksum"] == "sha256:" + hashlib.sha256(b"\x00\x01\x02\x03").hexdigest()
    data_arg = up.call_args.args[2]
    assert not isinstance(data_arg, (bytes, bytearray))
    assert up.call_args.kwargs["length"] == 4


def test_platform_endpoint_rejects_oversize_with_413(client, monkeypatch):
    monkeypatch.setattr(firmware_upload, "MAX_FIRMWARE_BYTES", 3)
    tenant_id = str(uuid.uuid4())
    with patch("app.routers.firmware.verify_token",
               return_value={"sub": str(uuid.uuid4()), "email": "a@b.c", "type": "platform"}), \
         patch("app.routers.firmware._validate_tenant", return_value=("t", tenant_id.replace("-", "_"))), \
         patch("app.routers.firmware.upload_firmware") as up:
        resp = client.post(f"/tenants/{tenant_id}/firmware", headers={"Authorization": "Bearer x"},
                           files={"file": ("fw.bin", b"\x00\x01\x02\x03", "application/octet-stream")},
                           data={"version": "1.0.0"})
    assert resp.status_code == 413
    up.assert_not_called()


def test_portal_endpoint_streams_and_rejects_oversize(client, monkeypatch):
    from app.services import tenant_session
    tenant = str(uuid.uuid4())
    token_claims = {"sub": str(uuid.uuid4()), "email": "a@b.c", "type": "tenant", "role": "admin",
                    "tenant_id": tenant, "token_type": "access"}
    with patch("app.services.tenant_session.verify_token", return_value=token_claims), \
         patch("app.routers.tenant_portal.upload_firmware", return_value="k") as up, \
         patch("app.routers.tenant_portal.add_firmware_tables_to_tenant_schema"), \
         patch("app.routers.tenant_portal.SessionLocal"):
        resp = client.post("/tenant-portal/me/firmware", cookies={"iot_token": "x"},
                           files={"file": ("fw.bin", b"abcd", "application/octet-stream")},
                           data={"version": "2.0.0"})
        assert resp.status_code == 201, resp.text
        assert not isinstance(up.call_args.args[2], (bytes, bytearray))
        assert up.call_args.kwargs["length"] == 4

        monkeypatch.setattr(firmware_upload, "MAX_FIRMWARE_BYTES", 3)
        up.reset_mock()
        resp = client.post("/tenant-portal/me/firmware", cookies={"iot_token": "x"},
                           files={"file": ("fw.bin", b"abcd", "application/octet-stream")},
                           data={"version": "2.0.0"})
        assert resp.status_code == 413
        up.assert_not_called()
