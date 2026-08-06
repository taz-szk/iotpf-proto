from unittest.mock import patch, MagicMock

_TENANT_CONFIG = {"org_id": "org-001", "token": "token-001"}

def test_ingest_telemetry_success(client):
    with patch("app.main.get_tenant_influx_config", return_value=_TENANT_CONFIG), \
         patch("app.main.write_telemetry") as mock_write, \
         patch("app.main.update_last_seen") as mock_status:

        resp = client.post("/ingest", json={
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "device_id": "device-001",
            "payload": {"temperature": 25.3, "humidity": 60.1},
            "topic_type": "telemetry",
        })

    assert resp.status_code == 200
    assert resp.json()["result"] == "written"
    assert mock_write.called
    assert mock_status.called

def test_ingest_status_update(client):
    with patch("app.main.get_tenant_influx_config", return_value=_TENANT_CONFIG), \
         patch("app.main.update_last_seen") as mock_status:

        resp = client.post("/ingest", json={
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "device_id": "device-001",
            "payload": {"status": "offline", "reason": "lwt"},
            "topic_type": "status",
        })

    assert resp.status_code == 200
    assert resp.json()["result"] == "status_updated"
    mock_status.assert_called_once_with(
        "123e4567-e89b-12d3-a456-426614174000", "device-001", "offline"
    )

def test_ingest_unknown_tenant(client):
    with patch("app.main.get_tenant_influx_config", return_value=None):
        resp = client.post("/ingest", json={
            "tenant_id": "00000000-0000-0000-0000-000000000000",
            "device_id": "device-001",
            "payload": {"temperature": 25.3},
        })
    assert resp.status_code == 404

def test_ingest_no_numeric_fields(client):
    with patch("app.main.get_tenant_influx_config", return_value=_TENANT_CONFIG), \
         patch("app.main.write_telemetry") as mock_write:

        resp = client.post("/ingest", json={
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "device_id": "device-001",
            "payload": {"label": "abc"},
        })

    assert resp.status_code == 200
    assert resp.json()["result"] == "no_numeric_fields"
    assert not mock_write.called

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
