import os
import tempfile
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app


def test_provision_returns_ca_certificate(tmp_path):
    ca_content = "-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----\n"
    ca_file = tmp_path / "root_ca.crt"
    ca_file.write_text(ca_content)

    mock_token = MagicMock()
    mock_token.is_active = True
    mock_token.expires_at = __import__('datetime').datetime(2099, 1, 1, tzinfo=__import__('datetime').timezone.utc)
    mock_token.registered_count = 0
    mock_token.max_devices = 10
    mock_token.tenant_id = __import__('uuid').UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    with patch("app.routers.provisioning.SessionLocal") as mock_db_cls, \
         patch("app.routers.provisioning.issue_device_cert_for_tenant",
               return_value=("CERT", "KEY")), \
         patch("app.config.settings.step_ca_root", str(ca_file)):

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        mock_db.execute.return_value.first.return_value = None
        mock_db_cls.return_value = mock_db

        client = TestClient(app)
        resp = client.post("/provision", json={
            "bootstrap_token": "tok",
            "device_id": "dev-001",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["ca_certificate"] == ca_content
