from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

USER_ID = "33333333-3333-3333-3333-333333333333"

def _token(role="admin"):
    # sub must be a UUID: write_audit_log() (called directly with the open db session by the
    # new endpoints, unlike the exception-swallowing log_audit() used elsewhere) parses actor_id
    # with uuid.UUID(...), so a non-UUID placeholder like "u1" would raise ValueError here.
    return create_access_token({"sub": USER_ID, "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": role})

def _session_ctx():
    db = MagicMock(); db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    return db

def test_issue_token_rejects_non_admin():
    resp = client.post("/tenant-portal/me/realtime-access-token",
                        cookies={"iot_token": _token("viewer")})
    assert resp.status_code == 403

def test_issue_token_returns_plaintext_once():
    tenant = MagicMock(influxdb_org_id="org-1")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.issue_readonly_token", return_value=("auth-1", "plain-token")):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.side_effect = [tenant, None]
        mock_session.return_value = db
        resp = client.post("/tenant-portal/me/realtime-access-token",
                            cookies={"iot_token": _token("admin")})
    assert resp.status_code == 201
    assert resp.json()["token"] == "plain-token"

def test_get_token_status_never_includes_token_value():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        row = MagicMock(created_at=None, revoked_at=None)
        db.query.return_value.filter.return_value.first.return_value = row
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/realtime-access-token",
                           cookies={"iot_token": _token("viewer")})
    assert resp.status_code == 200
    assert "token" not in resp.json()


def test_reissue_commits_old_row_deletion_even_if_new_issue_fails():
    from app.services.realtime_access import InfluxAuthorizationError
    tenant = MagicMock(influxdb_org_id="org-1")
    existing = MagicMock(influx_authorization_id="old-auth")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.revoke_readonly_token") as mock_revoke, \
         patch("app.routers.tenant_portal.issue_readonly_token", side_effect=InfluxAuthorizationError("boom")):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.side_effect = [tenant, existing]
        mock_session.return_value = db
        resp = client.post("/tenant-portal/me/realtime-access-token",
                            headers={"Authorization": f"Bearer {_token('admin')}"})
    assert resp.status_code == 502
    mock_revoke.assert_called_once_with("old-auth")
    db.delete.assert_called_once_with(existing)
    db.commit.assert_called()
