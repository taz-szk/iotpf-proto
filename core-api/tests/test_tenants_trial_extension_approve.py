from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _session_ctx(tenant):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant
    return mock_db


def test_approve_extends_trial_and_clears_flags():
    old_expiry = datetime.now(timezone.utc) + timedelta(days=1)
    tenant = MagicMock(
        id=TENANT_ID, slug="acme", status="active",
        created_at=datetime.now(timezone.utc), grafana_org_id=None,
        signup_source="self_signup", trial_expires_at=old_expiry,
        trial_extension_requested_at=datetime.now(timezone.utc),
        trial_warning_sent_at=datetime.now(timezone.utc),
    )
    tenant.name = "Acme"  # MagicMock(name=...)はmock自身の内部名になるため、属性は後で代入する
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.write_audit_log"):
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            f"/tenants/{TENANT_ID}/trial-extension/approve",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert tenant.trial_expires_at > old_expiry
    assert tenant.trial_extension_requested_at is None
    assert tenant.trial_warning_sent_at is None


def test_approve_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/trial-extension/approve")
    assert resp.status_code == 401


def test_approve_404_when_tenant_missing():
    with patch("app.routers.tenants.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx(None)
        resp = client.post(
            f"/tenants/{TENANT_ID}/trial-extension/approve",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404
