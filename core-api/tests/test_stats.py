import uuid
from unittest.mock import patch, MagicMock

def _platform_payload():
    return {"sub": str(uuid.uuid4()), "type": "platform"}

def test_get_stats_returns_expected_shape(client):
    tenant_id = str(uuid.uuid4())

    with patch("app.routers.stats.verify_token", return_value=_platform_payload()), \
         patch("app.routers.stats.SessionLocal") as mock_session, \
         patch("app.routers.stats._count_influxdb_points", return_value=42000), \
         patch("app.routers.stats._calc_provisionable_devices", return_value=(150, False)):

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
    assert "data_points_this_month" in data
    assert data["data_points_this_month"] == 42000
    assert "alert_events_this_month" in data
    assert "firmware_releases" in data
    assert data["provisionable_devices"] == 150
    assert data["has_unlimited_token"] is False

def test_get_stats_uses_calendar_month_for_alert_events(client):
    tenant_id = str(uuid.uuid4())

    with patch("app.routers.stats.verify_token", return_value=_platform_payload()), \
         patch("app.routers.stats.SessionLocal") as mock_session, \
         patch("app.routers.stats._count_influxdb_points", return_value=0), \
         patch("app.routers.stats._calc_provisionable_devices", return_value=(0, False)):

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
            f"/tenants/{tenant_id}/stats",
            headers={"Authorization": "Bearer dummy"},
        )

    assert resp.status_code == 200
    alert_sql = next(sql for sql in executed_sql if "alert_events" in sql)
    assert "date_trunc('month'" in alert_sql
    assert "30 days" not in alert_sql


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

def test_calc_provisionable_devices_sums_remaining_capacity():
    from app.routers.stats import _calc_provisionable_devices
    from datetime import datetime, timezone, timedelta

    tok1 = MagicMock(id=uuid.uuid4(), max_devices=100)
    tok2 = MagicMock(id=uuid.uuid4(), max_devices=50)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [tok1, tok2]

    def execute_side_effect(stmt, params=None, **kwargs):
        m = MagicMock()
        # tok1 has 30 registered devices, tok2 has 50 (fully used)
        m.scalar.return_value = 30 if params and params.get("tid") == str(tok1.id) else 50
        return m

    mock_db.execute.side_effect = execute_side_effect

    remaining, has_unlimited = _calc_provisionable_devices(mock_db, "tenant-1", "tenant_x")
    assert remaining == 70  # (100-30) + (50-50)
    assert has_unlimited is False


def test_calc_provisionable_devices_flags_unlimited_token():
    from app.routers.stats import _calc_provisionable_devices, _UNLIMITED_DEVICES

    tok_finite = MagicMock(id=uuid.uuid4(), max_devices=100)
    tok_unlimited = MagicMock(id=uuid.uuid4(), max_devices=_UNLIMITED_DEVICES)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [tok_finite, tok_unlimited]
    mock_db.execute.return_value.scalar.return_value = 20

    remaining, has_unlimited = _calc_provisionable_devices(mock_db, "tenant-1", "tenant_x")
    assert remaining == 80  # only the finite token counted
    assert has_unlimited is True


def test_calc_provisionable_devices_no_tokens_returns_zero():
    from app.routers.stats import _calc_provisionable_devices

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    remaining, has_unlimited = _calc_provisionable_devices(mock_db, "tenant-1", "tenant_x")
    assert remaining == 0
    assert has_unlimited is False


def test_count_influxdb_points_parses_csv():
    from app.routers.stats import _parse_influx_csv_scalar
    csv = """,result,table,_value\n,_result,0,12345\n"""
    assert _parse_influx_csv_scalar(csv) == 12345

def test_count_influxdb_points_returns_zero_on_empty():
    from app.routers.stats import _parse_influx_csv_scalar
    assert _parse_influx_csv_scalar("") == 0
