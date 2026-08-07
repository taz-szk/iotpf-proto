# simulator/device_worker.py
from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Callable, Optional

from iot_platform import IotClient


class DeviceWorker(threading.Thread):
    def __init__(
        self,
        device_id: str,
        api_url: str,
        broker_host: str,
        broker_port: int,
        bootstrap_token: str,
        cert_dir: str,
        event_queue: queue.Queue,
        ssl_verify: bool = True,
    ):
        super().__init__(daemon=True, name=f"worker-{device_id}")
        self.device_id = device_id
        self._api_url = api_url
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._bootstrap_token = bootstrap_token
        self._cert_dir = cert_dir
        self._queue = event_queue
        self._ssl_verify = ssl_verify
        self._client: Optional[IotClient] = None
        self._running = True
        self._connected_flag = threading.Event()
        self._stop_send = threading.Event()
        self._send_thread: Optional[threading.Thread] = None

    def run(self) -> None:
        self._put_event("status", {"state": "provisioning"})
        try:
            self._client = IotClient(
                self._api_url,
                broker_host=self._broker_host,
                broker_port=self._broker_port,
            )
            cert_pem = os.path.join(self._cert_dir, "cert.pem")
            if os.path.isdir(self._cert_dir) and os.path.exists(cert_pem):
                self._put_event("log", {"message": f"{self.device_id}: 証明書を再利用して接続中", "level": "info"})
                self._client.load_credentials(self._cert_dir)
            else:
                self._put_event("log", {"message": f"{self.device_id}: プロビジョニング中...", "level": "info"})
                self._client.provision(self._bootstrap_token, self.device_id, self._cert_dir, verify=self._ssl_verify)
                self._put_event("log", {"message": f"{self.device_id}: プロビジョニング完了", "level": "info"})

            self._put_event("status", {"state": "connecting"})
            self._client.connect()
            self._connected_flag.set()
            self._put_event("status", {"state": "connected"})
            self._put_event("log", {"message": f"{self.device_id}: 接続完了", "level": "info"})
        except Exception as exc:
            self._put_event("status", {"state": "error"})
            self._put_event("log", {"message": f"{self.device_id}: エラー — {exc}", "level": "error"})
            return

        while self._running:
            time.sleep(0.2)

        self._put_event("status", {"state": "disconnected"})

    def start_sending(self, interval: float, payload_fn: Callable[[], dict]) -> None:
        self.stop_sending()
        self._stop_send.clear()
        self._send_thread = threading.Thread(
            target=self._send_loop,
            args=(interval, payload_fn),
            daemon=True,
            name=f"send-{self.device_id}",
        )
        self._send_thread.start()

    def stop_sending(self) -> None:
        self._stop_send.set()
        if self._send_thread and self._send_thread.is_alive():
            self._send_thread.join(timeout=2)

    def stop(self) -> None:
        self._running = False
        self.stop_sending()
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def _send_loop(self, interval: float, payload_fn: Callable[[], dict]) -> None:
        while not self._stop_send.is_set():
            if self._client and self._connected_flag.is_set():
                try:
                    payload = payload_fn()
                    self._client.publish_telemetry(payload)
                    self._put_event("telemetry", {"payload": payload})
                except Exception as exc:
                    self._put_event(
                        "log", {"message": f"{self.device_id}: 送信エラー — {exc}", "level": "error"}
                    )
            self._stop_send.wait(interval)

    def _put_event(self, event_type: str, data: dict) -> None:
        self._queue.put((self.device_id, event_type, data))
