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
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = tenant
        db.query.return_value.filter.return_value.first.return_value = None
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
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = tenant
        db.query.return_value.filter.return_value.first.return_value = existing
        mock_session.return_value = db
        resp = client.post("/tenant-portal/me/realtime-access-token",
                            cookies={"iot_token": _token("admin")})
    assert resp.status_code == 502
    mock_revoke.assert_called_once_with("old-auth")
    db.delete.assert_called_once_with(existing)
    db.commit.assert_called()


def test_issue_token_rejects_operator():
    resp = client.post("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("operator")})
    assert resp.status_code == 403


def test_reissue_happy_path_revokes_old_and_adds_new_with_no_store():
    tenant = MagicMock(influxdb_org_id="org-1")
    existing = MagicMock(influx_authorization_id="old-auth")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.revoke_readonly_token") as mock_revoke, \
         patch("app.routers.tenant_portal.issue_readonly_token", return_value=("new-auth", "new-token")):
        db = _session_ctx()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = tenant
        db.query.return_value.filter.return_value.first.return_value = existing
        mock_session.return_value = db
        resp = client.post("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("admin")})
    assert resp.status_code == 201
    assert resp.json()["token"] == "new-token"
    assert resp.headers["cache-control"] == "no-store"
    mock_revoke.assert_called_once_with("old-auth")
    db.delete.assert_called_once_with(existing)


def test_delete_requires_admin():
    resp = client.delete("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("operator")})
    assert resp.status_code == 403


def test_delete_404_when_no_row():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = db
        resp = client.delete("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("admin")})
    assert resp.status_code == 404


def test_delete_keeps_row_when_influx_revoke_fails():
    from app.services.realtime_access import InfluxAuthorizationError
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.revoke_readonly_token", side_effect=InfluxAuthorizationError("x")):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = MagicMock(influx_authorization_id="a")
        mock_session.return_value = db
        resp = client.delete("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("admin")})
    assert resp.status_code == 502
    db.delete.assert_not_called()


def test_delete_success_deletes_row_and_audits():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.revoke_readonly_token") as mock_revoke:
        db = _session_ctx()
        row = MagicMock(influx_authorization_id="a")
        db.query.return_value.filter.return_value.first.return_value = row
        mock_session.return_value = db
        resp = client.delete("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("admin")})
    assert resp.status_code == 204
    mock_revoke.assert_called_once_with("a")
    db.delete.assert_called_once_with(row)
    db.commit.assert_called()


def test_issue_rolls_back_influx_authorization_if_db_commit_fails():
    tenant = MagicMock(influxdb_org_id="org-1")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.revoke_readonly_token") as mock_revoke, \
         patch("app.routers.tenant_portal.issue_readonly_token", return_value=("auth-9", "tok")):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = tenant
        db.commit.side_effect = RuntimeError("db down")
        mock_session.return_value = db
        resp = client.post("/tenant-portal/me/realtime-access-token", cookies={"iot_token": _token("admin")})
    assert resp.status_code == 500
    mock_revoke.assert_called_once_with("auth-9")
