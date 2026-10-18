from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _admin_token():
    return create_access_token({
        "sub": "user-id", "email": "admin@tenant.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": "admin",
    })


def _session_ctx(tenant):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant
    return mock_db


def test_trial_extension_request_rejects_non_self_signup_tenant():
    tenant = MagicMock(id=TENANT_ID, name="Acme", signup_source="platform_created")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            "/tenant-portal/me/trial-extension-request",
            cookies={"iot_token": _admin_token()},
        )
    assert resp.status_code == 400


def test_trial_extension_request_notifies_slack_and_sets_timestamp():
    tenant = MagicMock(id=TENANT_ID, name="Acme", signup_source="self_signup",
                       trial_extension_requested_at=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.post_slack_message") as mock_slack, \
         patch("app.routers.tenant_portal.settings") as mock_settings:
        mock_settings.vuln_scan_slack_webhook_url = "https://hooks.slack.com/services/T/B/X"
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            "/tenant-portal/me/trial-extension-request",
            cookies={"iot_token": _admin_token()},
        )
    assert resp.status_code == 200
    assert tenant.trial_extension_requested_at is not None
    assert mock_slack.called
    assert resp.json()["trial_extension_requested_at"] is not None


def test_trial_extension_request_skips_slack_when_webhook_not_configured():
    tenant = MagicMock(id=TENANT_ID, name="Acme", signup_source="self_signup",
                       trial_extension_requested_at=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.post_slack_message") as mock_slack, \
         patch("app.routers.tenant_portal.settings") as mock_settings:
        mock_settings.vuln_scan_slack_webhook_url = ""
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            "/tenant-portal/me/trial-extension-request",
            cookies={"iot_token": _admin_token()},
        )
    assert resp.status_code == 200
    mock_slack.assert_not_called()
