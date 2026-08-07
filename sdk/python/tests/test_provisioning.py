import os
import tempfile
from unittest.mock import patch, MagicMock
from iot_platform.provisioning import provision, load_credentials

FAKE_RESPONSE = {
    "tenant_id": "aaa-bbb",
    "device_id": "dev-001",
    "certificate": "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----\n",
    "private_key": "-----BEGIN EC PRIVATE KEY-----\nKEY\n-----END EC PRIVATE KEY-----\n",
    "ca_certificate": "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n",
}


def _mock_post(url, json, timeout):
    mock_resp = MagicMock()
    mock_resp.json.return_value = FAKE_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_provision_saves_all_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("requests.post", side_effect=_mock_post):
            t_id, d_id = provision("http://api", "tok123", "dev-001", tmpdir)

        assert t_id == "aaa-bbb"
        assert d_id == "dev-001"
        assert open(os.path.join(tmpdir, "cert.pem")).read() == FAKE_RESPONSE["certificate"]
        assert open(os.path.join(tmpdir, "key.pem")).read() == FAKE_RESPONSE["private_key"]
        assert open(os.path.join(tmpdir, "ca.pem")).read() == FAKE_RESPONSE["ca_certificate"]
        assert open(os.path.join(tmpdir, "tenant_id")).read() == "aaa-bbb"
        assert open(os.path.join(tmpdir, "device_id")).read() == "dev-001"


def test_provision_posts_correct_body():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("requests.post", side_effect=_mock_post) as mock_post:
            provision("http://api", "tok123", "dev-001", tmpdir)

        mock_post.assert_called_once_with(
            "http://api/provision",
            json={"bootstrap_token": "tok123", "device_id": "dev-001"},
            timeout=30,
        )


def test_load_credentials_returns_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "tenant_id"), "w").write("t-123")
        open(os.path.join(tmpdir, "device_id"), "w").write("dev-001")
        open(os.path.join(tmpdir, "cert.pem"), "w").write("cert")
        open(os.path.join(tmpdir, "key.pem"), "w").write("key")
        open(os.path.join(tmpdir, "ca.pem"), "w").write("ca")

        tid, cert_p, key_p, ca_p = load_credentials(tmpdir)

        assert tid == "t-123"
        assert cert_p == os.path.join(tmpdir, "cert.pem")
        assert key_p == os.path.join(tmpdir, "key.pem")
        assert ca_p == os.path.join(tmpdir, "ca.pem")


def test_load_credentials_missing_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            load_credentials(tmpdir)
            assert False, "Should raise FileNotFoundError"
        except FileNotFoundError:
            pass
