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


def test_platform_group_ota_db_write_failure_does_not_block_other_devices():
    # publish_ota_command succeeds for both devices, but the DB write (ota_events
    # INSERT / write_audit_log) fails for dev-1. Thanks to the per-device
    # SAVEPOINT (db.begin_nested()), that failure must not cascade into an
    # InFailedSqlTransaction-style failure for dev-2, whose publish also succeeded.
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.firmware.SessionLocal") as mock_sl, \
         patch("app.routers.firmware.publish_ota_command") as mock_publish, \
         patch("app.routers.firmware.write_audit_log") as mock_audit, \
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
        # Both publishes succeed.
        mock_publish.side_effect = [None, None]
        # write_audit_log raises for dev-1 only (simulating a DB write failure
        # after publish already succeeded), then succeeds for dev-2.
        mock_audit.side_effect = [Exception("db write failed"), None]

        resp = client.post(
            f"/tenants/{TENANT_ID}/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["device_id"] == "dev-1"
    assert results[0]["status"] == "failed"
    # dev-2's publish succeeded and its DB write must not be reported as
    # failed just because dev-1's SAVEPOINT was rolled back.
    assert results[1] == {"device_id": "dev-2", "status": "dispatched"}
    # The SAVEPOINT for the failed device must have been opened and rolled
    # back independently of the outer session/transaction.
    assert mock_db.begin_nested.call_count == 2
    mock_db.commit.assert_called_once()


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


def test_portal_group_ota_db_write_failure_does_not_block_other_devices():
    # Same scenario as the platform-side test: publish succeeds for both
    # devices, but the DB write fails for dev-1. The per-device SAVEPOINT
    # must isolate that failure so dev-2's successful publish is still
    # reported as "dispatched".
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.publish_ota_command") as mock_publish, \
         patch("app.routers.tenant_portal.write_audit_log") as mock_audit, \
         patch("app.routers.tenant_portal.create_firmware_download_token", return_value="tok"):
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
        mock_publish.side_effect = [None, None]
        mock_audit.side_effect = [Exception("db write failed"), None]

        resp = client.post(
            f"/tenant-portal/me/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            cookies={"iot_token": _tenant_token("operator")},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["device_id"] == "dev-1"
    assert results[0]["status"] == "failed"
    assert results[1] == {"device_id": "dev-2", "status": "dispatched"}
    assert mock_db.begin_nested.call_count == 2
    mock_db.commit.assert_called_once()
