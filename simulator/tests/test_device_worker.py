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
        MockClient.return_value.provision.assert_called_once_with(
            "tok", "test-001", cert_dir, verify=True, group_id=None)

    @patch("device_worker.IotClient")
    def test_provisions_with_group_id(self, MockClient):
        """group_id を渡した場合 provision() に group_id が渡される"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "test-001")
            q = queue.Queue()
            worker = _make_worker(cert_dir, q, group_id="group-abc")
            worker.start()
            time.sleep(0.4)
            worker.stop()
            worker.join(timeout=2)
        MockClient.return_value.provision.assert_called_once_with(
            "tok", "test-001", cert_dir, verify=True, group_id="group-abc")

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


class TestDeviceWorkerFwVersionAcrossRegistrations(unittest.TestCase):
    """fw_version ファイルは cert_dir(テナント名/デバイス名)単位で残る。
    過去に同じ名前で登録・OTAしたときの値が、新しい登録の送信データに混ざってはならない。"""

    @staticmethod
    def _write(path: str, text: str = "x") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    @staticmethod
    def _events(q: queue.Queue) -> list:
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    def _run_worker(self, cert_dir: str, MockClient) -> DeviceWorker:
        q = queue.Queue()
        worker = _make_worker(cert_dir, q)
        worker.start()
        time.sleep(0.4)
        worker.stop()
        worker.join(timeout=2)
        worker._test_events = self._events(q)
        return worker

    @patch("device_worker.IotClient")
    def test_fresh_registration_ignores_a_stale_fw_version_file(self, MockClient):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "tenant", "test-001")
            self._write(os.path.join(cert_dir, "fw_version"), "1.5.0")  # cert.pem は無い = 新規登録
            worker = self._run_worker(cert_dir, MockClient)
        MockClient.return_value.provision.assert_called_once()
        MockClient.return_value.publish_status.assert_called_once_with("online", fw_version="1.0.0")
        self.assertEqual(worker.fw_version, "1.0.0")

    @patch("device_worker.IotClient")
    def test_stale_fw_version_is_not_resurrected_by_a_later_restart(self, MockClient):
        """新規登録で上書きしないと、証明書ができた次の起動で古い値が復活する"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "tenant", "test-001")
            self._write(os.path.join(cert_dir, "fw_version"), "1.5.0")
            self._run_worker(cert_dir, MockClient)
            self._write(os.path.join(cert_dir, "cert.pem"))  # provision() が証明書を書いた状態を再現
            restarted = _make_worker(cert_dir, queue.Queue())
        self.assertEqual(restarted.fw_version, "1.0.0")

    @patch("device_worker.IotClient")
    def test_reused_credentials_keep_the_persisted_fw_version(self, MockClient):
        """OTA 済みの同じデバイスを再起動しただけなら、保存した版数を引き継ぐ"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "tenant", "test-001")
            self._write(os.path.join(cert_dir, "cert.pem"))
            self._write(os.path.join(cert_dir, "fw_version"), "1.5.0")
            worker = self._run_worker(cert_dir, MockClient)
        MockClient.return_value.provision.assert_not_called()
        MockClient.return_value.publish_status.assert_called_once_with("online", fw_version="1.5.0")
        self.assertEqual(worker.fw_version, "1.5.0")

    @patch("device_worker.IotClient")
    def test_worker_reports_its_actual_fw_version_to_the_ui(self, MockClient):
        """画面のラベルは固定の 1.0.0 ではなく、実際に送る版数を表示する"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_dir = os.path.join(tmpdir, "tenant", "test-001")
            self._write(os.path.join(cert_dir, "cert.pem"))
            self._write(os.path.join(cert_dir, "fw_version"), "1.5.0")
            worker = self._run_worker(cert_dir, MockClient)
        fw_events = [d for (_, t, d) in worker._test_events if t == "fw_version"]
        self.assertEqual(fw_events, [{"version": "1.5.0"}])


if __name__ == "__main__":
    unittest.main()
