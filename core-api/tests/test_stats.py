import uuid
from unittest.mock import patch, MagicMock

def _platform_payload():
    return {"sub": str(uuid.uuid4()), "type": "platform"}

def test_get_stats_returns_expected_shape(client):
    tenant_id = str(uuid.uuid4())

    with patch("app.routers.stats.verify_token", return_value=_platform_payload()), \
         patch("app.routers.stats.SessionLocal") as mock_session, \
         patch("app.routers.stats._count_influxdb_points", return_value=42000):

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
            f"/tenants/{tenant_id}/stats",
            headers={"Authorization": "Bearer dummy"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "total_devices" in data
    assert "online_devices" in data
    assert "data_points_30d" in data
    assert data["data_points_30d"] == 42000
    assert "alert_events_30d" in data
    assert "firmware_releases" in data

def test_get_stats_tenant_not_found(client):
    tenant_id = str(uuid.uuid4())

    with patch("app.routers.stats.verify_token", return_value=_platform_payload()), \
         patch("app.routers.stats.SessionLocal") as mock_session:

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = client.get(
            f"/tenants/{tenant_id}/stats",
            headers={"Authorization": "Bearer dummy"},
        )

    assert resp.status_code == 404

def test_count_influxdb_points_parses_csv():
    from app.routers.stats import _parse_influx_csv_scalar
    csv = """,result,table,_value\n,_result,0,12345\n"""
    assert _parse_influx_csv_scalar(csv) == 12345

def test_count_influxdb_points_returns_zero_on_empty():
    from app.routers.stats import _parse_influx_csv_scalar
    assert _parse_influx_csv_scalar("") == 0
