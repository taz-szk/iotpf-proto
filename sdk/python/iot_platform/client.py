import json
import os
import ssl
import threading
from typing import Callable, Optional
import paho.mqtt.client as mqtt
from .provisioning import provision as _do_provision, load_credentials
from .ota import OtaHandler  # re-exported for convenience


class IotClient:
    def __init__(self, api_url: str, broker_host: str, broker_port: int = 8883):
        self._api_url = api_url
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._tenant_id: Optional[str] = None
        self._device_id: Optional[str] = None
        self._cert_dir: Optional[str] = None
        self._command_callback: Optional[Callable] = None
        self._disconnect_callback: Optional[Callable] = None
        self._mqtt: Optional[mqtt.Client] = None
        self._connected = threading.Event()
        self._initial_connect_done = False

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None:
        """予期しない切断（rc != 0）を通知するコールバックを登録する。"""
        self._disconnect_callback = callback

    def provision(self, bootstrap_token: str, device_id: str, cert_dir: str, verify: bool = True) -> None:
        tenant_id, dev_id = _do_provision(self._api_url, bootstrap_token, device_id, cert_dir, verify=verify)
        self._tenant_id = tenant_id
        self._device_id = dev_id
        self._cert_dir = cert_dir

    def load_credentials(self, cert_dir: str) -> None:
        tenant_id, _, _, _ = load_credentials(cert_dir)
        self._tenant_id = tenant_id
        self._cert_dir = cert_dir
        dev_id_path = os.path.join(cert_dir, "device_id")
        if os.path.exists(dev_id_path):
            with open(dev_id_path) as f:
                self._device_id = f.read().strip()

    def connect(self) -> None:
        if not self._tenant_id or not self._device_id:
            raise RuntimeError("Call provision() or load_credentials() first")

        client_id = f"{self._tenant_id}:{self._device_id}"
        self._mqtt = mqtt.Client(client_id=client_id)
        self._mqtt.username_pw_set(client_id, "")

        _, cert_path, key_path, ca_path = load_credentials(self._cert_dir)
        self._mqtt.tls_set(
            ca_certs=ca_path,
            certfile=cert_path,
            keyfile=key_path,
            tls_version=ssl.PROTOCOL_TLS,
        )

        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.on_disconnect = self._on_disconnect
        self._connected.clear()
        self._mqtt.connect(self._broker_host, self._broker_port, keepalive=60)
        self._mqtt.loop_start()

        if not self._connected.wait(timeout=30):
            self._mqtt.loop_stop()
            raise ConnectionError("MQTT connection timed out after 30s")

        self._initial_connect_done = True
        self.publish_status("online")

    def disconnect(self) -> None:
        if self._mqtt:
            self.publish_status("offline")
            self._mqtt.loop_stop()
            self._mqtt.disconnect()

    def force_disconnect(self) -> None:
        """offline を送信して MQTT 切断。loop は停止させ reconnect() に備える。"""
        if not self._mqtt:
            return
        try:
            self.publish_status("offline")
        except Exception:
            pass
        try:
            self._mqtt.disconnect()
        except Exception:
            pass

    def force_connect(self) -> bool:
        """新規MQTTクライアントを作成して再接続する。成功で True。"""
        if not self._tenant_id or not self._device_id or not self._cert_dir:
            return False
        if self._mqtt:
            try:
                self._mqtt.loop_stop()
            except Exception:
                pass
        try:
            client_id = f"{self._tenant_id}:{self._device_id}"
            self._mqtt = mqtt.Client(client_id=client_id)
            self._mqtt.username_pw_set(client_id, "")
            _, cert_path, key_path, ca_path = load_credentials(self._cert_dir)
            self._mqtt.tls_set(
                ca_certs=ca_path,
                certfile=cert_path,
                keyfile=key_path,
                tls_version=ssl.PROTOCOL_TLS,
            )
            self._mqtt.on_connect = self._on_connect
            self._mqtt.on_message = self._on_message
            self._mqtt.on_disconnect = self._on_disconnect
            self._connected.clear()
            self._mqtt.connect(self._broker_host, self._broker_port, keepalive=60)
            self._mqtt.loop_start()
            return self._connected.wait(timeout=15)
        except Exception:
            return False

    def publish_telemetry(self, measurements: dict) -> None:
        topic = f"/{self._tenant_id}/devices/{self._device_id}/telemetry"
        self._mqtt.publish(topic, json.dumps(measurements), qos=1)

    def publish_status(self, status: str = "online", fw_version: str | None = None) -> None:
        topic = f"/{self._tenant_id}/devices/{self._device_id}/status"
        payload: dict = {"status": status}
        if fw_version is not None:
            payload["fw_version"] = fw_version
        self._mqtt.publish(topic, json.dumps(payload), qos=1)

    def on_command(self, callback: Callable[[str, dict], None]) -> None:
        self._command_callback = callback

    def loop_start(self) -> None:
        if self._mqtt:
            self._mqtt.loop_start()

    def loop_stop(self) -> None:
        if self._mqtt:
            self._mqtt.loop_stop()

    def loop_forever(self) -> None:
        if self._mqtt:
            self._mqtt.loop_forever()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = f"/{self._tenant_id}/devices/{self._device_id}/commands"
            client.subscribe(topic, qos=1)
            self._connected.set()
            if self._initial_connect_done:
                # 再接続時: online ステータスを再パブリッシュ
                self.publish_status("online")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0 and self._disconnect_callback:
            # rc=0 は正常切断（disconnect() 呼び出し）なので無視
            self._disconnect_callback()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if self._command_callback:
            self._command_callback(payload.get("type", ""), payload)
