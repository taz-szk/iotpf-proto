from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.device_groups import GroupNotFoundError

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _cookie(role="viewer"):
    token = create_access_token({"sub": "u1", "email": "a@acme.com", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}

def test_device_locations_marks_old_position_as_stale():
    fresh = {"device_id": "d1", "device_name": "D1", "latitude": 35.0, "longitude": 139.0,
              "last_seen_at": datetime.now(timezone.utc) - timedelta(minutes=10)}
    stale = {"device_id": "d2", "device_name": "D2", "latitude": 34.0, "longitude": 138.0,
              "last_seen_at": datetime.now(timezone.utc) - timedelta(hours=2)}
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal._query_latest_device_locations", return_value=[fresh, stale]):
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get("/tenant-portal/me/device-locations", cookies=_cookie())
    assert resp.status_code == 200
    body = {d["device_id"]: d for d in resp.json()}
    assert body["d1"]["stale"] is False
    assert body["d2"]["stale"] is True

_GROUP_UUID = "22222222-2222-2222-2222-222222222222"

def test_device_locations_filters_by_group():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal._query_latest_device_locations", return_value=[]) as mock_query, \
         patch("app.routers.tenant_portal.list_group_device_ids", return_value=["d1", "d2"]) as mock_group:
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get(f"/tenant-portal/me/device-locations?group_id={_GROUP_UUID}", cookies=_cookie())
    assert resp.status_code == 200
    mock_group.assert_called_once()
    assert mock_query.call_args[0][1] == ["d1", "d2"]

def test_device_locations_rejects_non_uuid_group_id():
    resp = client.get("/tenant-portal/me/device-locations?group_id=not-a-uuid", cookies=_cookie())
    assert resp.status_code == 422

def test_query_latest_device_locations_sorts_before_last_for_deterministic_result():
    # Flux の group()+last() は並び順を保証しないため、明示的にsort(columns:["_time"])を
    # 挟んでから last() を取ること(そうしないと「最後の行」が必ずしも最新とは限らない)
    from app.routers.tenant_portal import _query_latest_device_locations
    tenant = MagicMock(influxdb_org_id="org1", influxdb_token="tok1")
    mock_resp = MagicMock(status_code=200, text="")
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        _query_latest_device_locations(tenant, None)
    sent_query = mock_post.call_args.kwargs["json"]["query"]
    assert sent_query.index('sort(columns: ["_time"])') < sent_query.index("last()")

def test_device_locations_returns_404_for_unknown_group():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.list_group_device_ids", side_effect=GroupNotFoundError(_GROUP_UUID)):
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get(f"/tenant-portal/me/device-locations?group_id={_GROUP_UUID}", cookies=_cookie())
    assert resp.status_code == 404
