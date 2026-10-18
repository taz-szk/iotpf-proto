from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _cookie():
    token = create_access_token({"sub": "u1", "email": "a@acme.com", "tenant_id": TENANT_ID, "role": "viewer", "type": "tenant"})
    return {"iot_token": token}

def _iso(dt):
    return dt.isoformat()

def test_location_history_rejects_range_over_7_days():
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(days=8))
    end = _iso(now)
    resp = client.get("/tenant-portal/me/devices/d1/location-history",
                       params={"start": start, "end": end}, cookies=_cookie())
    assert resp.status_code == 422

def test_location_history_returns_404_for_device_in_another_tenant():
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(hours=1))
    end = _iso(now)
    with patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        resp = client.get("/tenant-portal/me/devices/d1/location-history",
                           params={"start": start, "end": end}, cookies=_cookie())
    assert resp.status_code == 404

def test_location_history_returns_points_for_valid_device():
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(hours=1))
    end = _iso(now)
    point = {"time": now.isoformat(), "latitude": 35.0, "longitude": 139.0}
    with patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal._query_location_history", return_value=[point]):
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = MagicMock()
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get("/tenant-portal/me/devices/d1/location-history",
                           params={"start": start, "end": end}, cookies=_cookie())
    assert resp.status_code == 200
    assert resp.json() == [point]
