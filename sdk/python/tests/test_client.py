import json
import os
import tempfile
from unittest.mock import patch, MagicMock, call
from iot_platform.client import IotClient


def _setup_creds(tmpdir: str) -> IotClient:
    for name, content in [
        ("tenant_id", "t-abc"),
        ("device_id", "dev-001"),
        ("cert.pem", "CERT"),
        ("key.pem", "KEY"),
        ("ca.pem", "CA"),
    ]:
        open(os.path.join(tmpdir, name), "w").write(content)
    c = IotClient("http://api", "broker.local")
    c.load_credentials(tmpdir)
    return c


def test_load_credentials_sets_fields():
    with tempfile.TemporaryDirectory() as d:
        c = _setup_creds(d)
        assert c._tenant_id == "t-abc"
        assert c._device_id == "dev-001"
        assert c._cert_dir == d


def test_publish_telemetry_correct_topic():
    with tempfile.TemporaryDirectory() as d:
        c = _setup_creds(d)
        mock_mqtt = MagicMock()
        c._mqtt = mock_mqtt

        c.publish_telemetry({"temperature": 25.3, "humidity": 60.0})

        mock_mqtt.publish.assert_called_once_with(
            "/t-abc/devices/dev-001/telemetry",
            json.dumps({"temperature": 25.3, "humidity": 60.0}),
            qos=1,
        )


def test_publish_status_correct_topic():
    with tempfile.TemporaryDirectory() as d:
        c = _setup_creds(d)
        mock_mqtt = MagicMock()
        c._mqtt = mock_mqtt

        c.publish_status("online")

        mock_mqtt.publish.assert_called_once_with(
            "/t-abc/devices/dev-001/status",
            json.dumps({"status": "online"}),
            qos=1,
        )


def test_on_command_callback_dispatched():
    with tempfile.TemporaryDirectory() as d:
        c = _setup_creds(d)
        received = []
        c.on_command(lambda t, p: received.append((t, p)))

        mock_msg = MagicMock()
        mock_msg.payload = json.dumps({"type": "ota", "firmware_id": "uuid-1"}).encode()
        c._on_message(None, None, mock_msg)

        assert len(received) == 1
        assert received[0][0] == "ota"
        assert received[0][1]["firmware_id"] == "uuid-1"


def test_on_message_invalid_json_ignored():
    with tempfile.TemporaryDirectory() as d:
        c = _setup_creds(d)
        received = []
        c.on_command(lambda t, p: received.append(p))

        mock_msg = MagicMock()
        mock_msg.payload = b"not-json{{{"
        c._on_message(None, None, mock_msg)

        assert received == []


def test_disconnect_publishes_offline():
    with tempfile.TemporaryDirectory() as d:
        c = _setup_creds(d)
        mock_mqtt = MagicMock()
        c._mqtt = mock_mqtt

        c.disconnect()

        mock_mqtt.publish.assert_called_once_with(
            "/t-abc/devices/dev-001/status",
            json.dumps({"status": "offline"}),
            qos=1,
        )
        mock_mqtt.loop_stop.assert_called_once()
        mock_mqtt.disconnect.assert_called_once()
