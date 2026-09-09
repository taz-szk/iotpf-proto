from unittest.mock import patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
FIRMWARE_ID = "22222222-2222-2222-2222-222222222222"
GROUP_ID = "33333333-3333-3333-3333-333333333333"


def _platform_token():
    return create_access_token({
        "sub": "44444444-4444-4444-4444-444444444444", "email": "admin@iot.local", "type": "platform",
    })


def _tenant_token(role: str = "operator"):
    return create_access_token({
        "sub": "55555555-5555-5555-5555-555555555555", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _firmware_row():
    return MagicMock(minio_key="key", version="1.0.0", checksum="sha256:abc", file_size=100)


def test_platform_group_ota_partial_failure():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.firmware.SessionLocal") as mock_sl, \
         patch("app.routers.firmware.publish_ota_command") as mock_publish, \
         patch("app.routers.firmware.create_firmware_download_token", return_value="tok"):
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = MagicMock(id=GROUP_ID)
        svc_conn.execute.return_value.fetchall.return_value = [
            MagicMock(device_id="dev-1"), MagicMock(device_id="dev-2"),
        ]
        mock_svc_engine.connect.return_value = svc_conn

        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.execute.return_value.fetchone.return_value = _firmware_row()
        mock_publish.side_effect = [None, Exception("mqtt down")]

        resp = client.post(
            f"/tenants/{TENANT_ID}/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0] == {"device_id": "dev-1", "status": "dispatched"}
    assert results[1]["device_id"] == "dev-2"
    assert results[1]["status"] == "failed"


def test_platform_group_ota_group_not_found():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.firmware.SessionLocal") as mock_sl:
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = None
        mock_svc_engine.connect.return_value = svc_conn
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock(
            id=TENANT_ID, name="Tenant A",
        )
        resp = client.post(
            f"/tenants/{TENANT_ID}/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_portal_group_ota_requires_operator_or_admin():
    resp = client.post(
        f"/tenant-portal/me/groups/{GROUP_ID}/ota",
        json={"firmware_id": FIRMWARE_ID},
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403


def test_platform_group_ota_rejects_invalid_group_id_format():
    resp = client.post(
        f"/tenants/{TENANT_ID}/groups/not-a-uuid/ota",
        json={"firmware_id": FIRMWARE_ID},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_portal_group_ota_rejects_invalid_group_id_format():
    resp = client.post(
        f"/tenant-portal/me/groups/not-a-uuid/ota",
        json={"firmware_id": FIRMWARE_ID},
        cookies={"iot_token": _tenant_token("operator")},
    )
    assert resp.status_code == 422


def test_portal_group_ota_all_succeed():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.publish_ota_command") as mock_publish, \
         patch("app.routers.tenant_portal.create_firmware_download_token", return_value="tok"):
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = MagicMock(id=GROUP_ID)
        svc_conn.execute.return_value.fetchall.return_value = [MagicMock(device_id="dev-1")]
        mock_svc_engine.connect.return_value = svc_conn

        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.execute.return_value.fetchone.return_value = _firmware_row()

        resp = client.post(
            f"/tenant-portal/me/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            cookies={"iot_token": _tenant_token("operator")},
        )
    assert resp.status_code == 200
    assert resp.json()["results"] == [{"device_id": "dev-1", "status": "dispatched"}]
