import os
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

from device_worker import DeviceWorker


def _make_worker(cert_dir: str, event_queue: queue.Queue, **kwargs) -> DeviceWorker:
    defaults = dict(
        wid=0,
        device_id="test-001",
        api_url="https://localhost/api",
        broker_host="localhost",
        broker_port=8883,
        bootstrap_token="tok",
        cert_dir=cert_dir,
        event_queue=event_queue,
    )
    defaults.update(kwargs)
    return DeviceWorker(**defaults)


class TestDeviceWorkerProvisioning(unittest.TestCase):

    @patch("device_worker.IotClient")
    def test_provisions_when_no_cert_dir(self, MockClient):
        """cert.pem が存在しないとき provision() が呼ばれる"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")  # 存在しない
            q = queue.Queue()
            worker = _make_worker(cert_dir, q)
            worker.start()
            time.sleep(0.4)
            worker.stop()
            worker.join(timeout=2)
        MockClient.return_value.provision.assert_called_once_with("tok", "test-001", cert_dir, verify=True)

    @patch("device_worker.IotClient")
    def test_loads_creds_when_cert_exists(self, MockClient):
        """cert.pem が存在するとき load_credentials() が呼ばれ provision() は呼ばれない"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            os.makedirs(cert_dir)
            open(os.path.join(cert_dir, "cert.pem"), "w").close()
            q = queue.Queue()
            worker = _make_worker(cert_dir, q)
            worker.start()
            time.sleep(0.4)
            worker.stop()
            worker.join(timeout=2)
        MockClient.return_value.load_credentials.assert_called_once_with(cert_dir)
        MockClient.return_value.provision.assert_not_called()


class TestDeviceWorkerEvents(unittest.TestCase):

    @patch("device_worker.IotClient")
    def test_connected_event_emitted(self, MockClient):
        """接続成功後に status=connected イベントがキューに積まれる"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            worker = _make_worker(cert_dir, q)
            worker.start()
            time.sleep(0.4)
            worker.stop()
            worker.join(timeout=2)
        events = list(q.queue)
        states = [d["state"] for _, typ, d in events if typ == "status"]
        self.assertIn("connected", states)

    @patch("device_worker.IotClient")
    def test_error_event_on_connect_failure(self, MockClient):
        """IotClient.connect() が例外を投げたとき status=error イベントが積まれる"""
        MockClient.return_value.connect.side_effect = ConnectionError("timeout")
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            worker = _make_worker(cert_dir, q)
            worker.start()
            worker.join(timeout=2)
        events = list(q.queue)
        states = [d["state"] for _, typ, d in events if typ == "status"]
        self.assertIn("error", states)


class TestDeviceWorkerSending(unittest.TestCase):

    @patch("device_worker.IotClient")
    def test_telemetry_events_emitted_while_sending(self, MockClient):
        """start_sending 後にテレメトリイベントがキューに積まれる"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            worker = _make_worker(cert_dir, q)
            worker.start()
            time.sleep(0.3)
            worker.start_sending(0.05, lambda: {"v": 42})
            time.sleep(0.4)
            worker.stop_sending()
            worker.stop()
            worker.join(timeout=2)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        tel = [d for _, typ, d in events if typ == "telemetry"]
        self.assertGreater(len(tel), 0)
        self.assertIn("v", tel[0]["payload"])
        self.assertEqual(tel[0]["payload"]["v"], 42)
        self.assertIn("fw_version", tel[0]["payload"])

    @patch("device_worker.IotClient")
    def test_stop_sending_halts_telemetry(self, MockClient):
        """stop_sending 後は新たなテレメトリイベントが積まれない"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            worker = _make_worker(cert_dir, q)
            worker.start()
            time.sleep(0.2)
            worker.start_sending(0.05, lambda: {"v": 1})
            time.sleep(0.2)
            worker.stop_sending()
            while not q.empty():  # キュークリア
                q.get_nowait()
            time.sleep(0.2)
            remaining = []
            while not q.empty():
                remaining.append(q.get_nowait())
            tel_after = [d for _, typ, d in remaining if typ == "telemetry"]
            self.assertEqual(len(tel_after), 0)
            worker.stop()
            worker.join(timeout=2)


class TestDeviceWorkerOta(unittest.TestCase):

    def test_fw_version_default(self):
        """fw_version の初期値が "1.0.0" であること"""
        with tempfile.TemporaryDirectory() as tmpdir:
            q = queue.Queue()
            w = _make_worker(os.path.join(tmpdir, "dev"), q)
        self.assertEqual(w.fw_version, "1.0.0")

    def test_handle_command_ota_puts_event(self):
        """_handle_command("ota", payload) で ota_start イベントがキューに積まれる"""
        with tempfile.TemporaryDirectory() as tmpdir:
            q = queue.Queue()
            w = _make_worker(os.path.join(tmpdir, "dev"), q)
            payload = {
                "firmware_id": "fw-abc",
                "version": "2.0.0",
                "download_url": "https://example.com/fw.bin",
                "checksum": "sha256:deadbeef",
                "file_size": 1024,
            }
            w._handle_command("ota", payload)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        types = [typ for _, typ, _ in events]
        self.assertIn("ota_start", types)
        _, _, data = next((e for e in events if e[1] == "ota_start"), (None, None, {}))
        self.assertEqual(data["device_id"], "test-001")
        self.assertEqual(data["payload"], payload)
        self.assertIn("ssl_verify", data)

    def test_handle_command_unknown_puts_log(self):
        """未知のコマンドタイプはログイベントになり ota_start は積まれない"""
        with tempfile.TemporaryDirectory() as tmpdir:
            q = queue.Queue()
            w = _make_worker(os.path.join(tmpdir, "dev"), q)
            w._handle_command("unknown_cmd", {})

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        types = [typ for _, typ, _ in events]
        self.assertNotIn("ota_start", types)
        self.assertIn("log", types)

    @patch("device_worker.IotClient")
    def test_telemetry_includes_fw_version(self, MockClient):
        """テレメトリペイロードに fw_version キーが含まれること"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            w = _make_worker(cert_dir, q)
            w.start()
            time.sleep(0.3)
            w.start_sending(0.05, lambda: {"temp": 25.0})
            time.sleep(0.3)
            w.stop_sending()
            w.stop()
            w.join(timeout=2)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        tel = [d for _, typ, d in events if typ == "telemetry"]
        self.assertGreater(len(tel), 0)
        self.assertIn("fw_version", tel[0]["payload"])
        self.assertEqual(tel[0]["payload"]["fw_version"], "1.0.0")

    @patch("device_worker.IotClient")
    def test_telemetry_reflects_updated_fw_version(self, MockClient):
        """fw_version を変更すると次のテレメトリに反映される"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            w = _make_worker(cert_dir, q)
            w.start()
            time.sleep(0.3)
            w.fw_version = "2.0.0"
            w.start_sending(0.05, lambda: {"temp": 25.0})
            time.sleep(0.3)
            w.stop_sending()
            w.stop()
            w.join(timeout=2)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        tel = [d for _, typ, d in events if typ == "telemetry"]
        self.assertGreater(len(tel), 0)
        self.assertEqual(tel[0]["payload"]["fw_version"], "2.0.0")


if __name__ == "__main__":
    unittest.main()
