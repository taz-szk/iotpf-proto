import uuid
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_jwt(role="viewer"):
    return create_access_token({
        "sub": "user-id",
        "email": "user@acme.com",
        "type": "tenant",
        "role": role,
        "tenant_id": _TENANT_ID,
    })


def test_get_stats_returns_expected_shape():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal._count_influxdb_points", return_value=42000), \
         patch("app.routers.tenant_portal._calc_provisionable_devices", return_value=(150, False)):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db

        mock_tenant = MagicMock()
        mock_tenant.influxdb_org_id = "org-001"
        mock_tenant.influxdb_token = "tok-001"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_tenant

        def execute_side_effect(stmt, *args, **kwargs):
            m = MagicMock()
            m.scalar.return_value = 5
            return m

        mock_db.execute.side_effect = execute_side_effect

        resp = client.get(
            "/tenant-portal/me/stats",
            cookies={"iot_token": _tenant_jwt()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "total_devices" in data
    assert "online_devices" in data
    assert "data_points_this_month" in data
    assert data["data_points_this_month"] == 42000
    assert "alert_events_this_month" in data
    assert "firmware_releases" in data
    assert data["provisionable_devices"] == 150
    assert data["has_unlimited_token"] is False


def test_get_stats_uses_calendar_month_for_alert_events():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal._count_influxdb_points", return_value=0), \
         patch("app.routers.tenant_portal._calc_provisionable_devices", return_value=(0, False)):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db

        mock_tenant = MagicMock()
        mock_tenant.influxdb_org_id = "org-001"
        mock_tenant.influxdb_token = "tok-001"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_tenant

        executed_sql = []

        def execute_side_effect(stmt, *args, **kwargs):
            executed_sql.append(str(stmt))
            m = MagicMock()
            m.scalar.return_value = 5
            return m

        mock_db.execute.side_effect = execute_side_effect

        resp = client.get(
            "/tenant-portal/me/stats",
            cookies={"iot_token": _tenant_jwt()},
        )

    assert resp.status_code == 200
    alert_sql = next(sql for sql in executed_sql if "alert_events" in sql)
    assert "date_trunc('month'" in alert_sql
    assert "30 days" not in alert_sql


def test_get_stats_requires_auth():
    resp = client.get("/tenant-portal/me/stats")
    assert resp.status_code == 401


def test_get_stats_tenant_not_found():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = client.get(
            "/tenant-portal/me/stats",
            cookies={"iot_token": _tenant_jwt()},
        )

    assert resp.status_code == 404
