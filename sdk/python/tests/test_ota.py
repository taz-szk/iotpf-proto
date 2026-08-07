import hashlib
import os
import tempfile
from unittest.mock import patch, MagicMock
from iot_platform.ota import OtaHandler


def _mock_get(content: bytes):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.iter_content.return_value = [content]
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_download_success():
    content = b"firmware binary"
    sha256 = hashlib.sha256(content).hexdigest()

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "fw.bin")
        with patch("requests.get", return_value=_mock_get(content)):
            result = OtaHandler.download_and_verify("http://x/fw.bin", out, sha256)

        assert result is True
        assert open(out, "rb").read() == content


def test_download_wrong_checksum():
    content = b"firmware binary"
    wrong_sha = "0" * 64

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "fw.bin")
        with patch("requests.get", return_value=_mock_get(content)):
            result = OtaHandler.download_and_verify("http://x/fw.bin", out, wrong_sha)

        assert result is False


def test_download_sha256_prefix_accepted():
    content = b"firmware binary"
    sha256 = "sha256:" + hashlib.sha256(content).hexdigest()

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "fw.bin")
        with patch("requests.get", return_value=_mock_get(content)):
            result = OtaHandler.download_and_verify("http://x/fw.bin", out, sha256)

        assert result is True


def test_handle_reads_payload():
    content = b"fw"
    sha256 = hashlib.sha256(content).hexdigest()
    payload = {
        "type": "ota",
        "firmware_id": "uuid-1",
        "version": "1.0.0",
        "download_url": "http://x/fw.bin",
        "checksum": f"sha256:{sha256}",
        "file_size": len(content),
    }

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "fw.bin")
        with patch("requests.get", return_value=_mock_get(content)):
            result = OtaHandler.handle(payload, out)

        assert result is True
