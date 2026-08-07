# Windows デバイスシミュレーター 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** IoT Platform に対してデバイスの登録（プロビジョニング）とテレメトリ送信を行う Windows 向け Tkinter GUI シミュレーターを作成する。

**Architecture:** `device_worker.py` が 1 デバイス = 1 スレッドの IotClient ラッパーとして機能し、`queue.Queue` 経由でイベントを発行する。`simulator.py` の Tkinter メインループが 100ms ごとにキューをポーリングして UI を更新する。既存の `sdk/python/iot_platform` パッケージを再利用する。

**Tech Stack:** Python 3.8+, Tkinter（標準ライブラリ）, paho-mqtt 1.6.1, requests, threading / queue（標準ライブラリ）

## Global Constraints

- Python 3.8 以上、Windows 10/11 で動作すること（追加ランタイム不要）
- 外部依存は `paho-mqtt==1.6.1` と `requests` のみ
- 既存 `sdk/python/iot_platform` パッケージを `pip install -e sdk/python` でインストールして import する
- 証明書保存先: `simulator/certs/{device_id}/` — `cert.pem`, `key.pem`, `ca.pem`, `device_id` の 4 ファイル
- MQTT client_id および username: `{tenant_id}:{device_id}`（空パスワード）
- MQTT ブローカーポート: 8883（mTLS 必須）
- テレメトリトピック: `/{tenant_id}/devices/{device_id}/telemetry`
- ステータストピック: `/{tenant_id}/devices/{device_id}/status`
- スレッド間通信は `queue.Queue` + Tkinter `after(100, ...)` ポーリングのみ（UI オブジェクトへの直接操作はメインスレッドのみ）
- キューイベント形式: `(device_id: str, event_type: str, data: dict)`
- event_type は `"status"` / `"telemetry"` / `"log"` の 3 種のみ
- `"status"` の `data["state"]` は `"provisioning"` / `"connecting"` / `"connected"` / `"disconnected"` / `"error"` の 5 値のみ

---

### Task 1: DeviceWorker + requirements.txt

**Files:**
- Create: `simulator/requirements.txt`
- Create: `simulator/device_worker.py`
- Create: `simulator/tests/__init__.py`
- Create: `simulator/tests/conftest.py`
- Create: `simulator/tests/test_device_worker.py`

**Interfaces:**
- Consumes: `iot_platform.IotClient(api_url, broker_host, broker_port)` — `.provision(token, device_id, cert_dir)`, `.load_credentials(cert_dir)`, `.connect()`, `.disconnect()`, `.publish_telemetry(payload: dict)`
- Produces:
  - `DeviceWorker(device_id: str, api_url: str, broker_host: str, broker_port: int, bootstrap_token: str, cert_dir: str, event_queue: queue.Queue)` — `threading.Thread` サブクラス、`daemon=True`
  - `worker.start()` — スレッド開始（provisioning → connect → idle ループ）
  - `worker.start_sending(interval: float, payload_fn: Callable[[], dict]) -> None`
  - `worker.stop_sending() -> None`
  - `worker.stop() -> None` — 送信停止 + MQTT disconnect + スレッド終了
  - キューへのイベント: `(device_id, "status", {"state": str})`, `(device_id, "telemetry", {"payload": dict})`, `(device_id, "log", {"message": str, "level": str})`

- [ ] **Step 1: `simulator/requirements.txt` を作成する**

```
paho-mqtt==1.6.1
requests
```

- [ ] **Step 2: `simulator/tests/conftest.py` と `__init__.py` を作成する**

`simulator/tests/conftest.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

`simulator/tests/__init__.py`: （空ファイル）

- [ ] **Step 3: テストファイルを作成する**

`simulator/tests/test_device_worker.py`:

```python
import os
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

from device_worker import DeviceWorker


def _make_worker(cert_dir: str, event_queue: queue.Queue, **kwargs) -> DeviceWorker:
    defaults = dict(
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
        MockClient.return_value.provision.assert_called_once_with("tok", "test-001", cert_dir)

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
        self.assertEqual(tel[0]["payload"], {"v": 42})

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: テストが FAIL することを確認する**

```powershell
cd C:\...\iot-platform
pip install -e sdk/python
pip install paho-mqtt==1.6.1 requests
cd simulator
pytest tests/test_device_worker.py -v
```

期待: `ModuleNotFoundError: No module named 'device_worker'` などで全テスト FAIL

- [ ] **Step 5: `simulator/device_worker.py` を実装する**

```python
# simulator/device_worker.py
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
    ):
        super().__init__(daemon=True, name=f"worker-{device_id}")
        self.device_id = device_id
        self._api_url = api_url
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._bootstrap_token = bootstrap_token
        self._cert_dir = cert_dir
        self._queue = event_queue
        self._client: Optional[IotClient] = None
        self._running = True
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
                self._client.provision(self._bootstrap_token, self.device_id, self._cert_dir)
                self._put_event("log", {"message": f"{self.device_id}: プロビジョニング完了", "level": "info"})

            self._put_event("status", {"state": "connecting"})
            self._client.connect()
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
            if self._client:
                try:
                    payload = payload_fn()
                    self._client.publish_telemetry(payload)
                    self._put_event("telemetry", {"payload": payload})
                    self._put_event(
                        "log",
                        {
                            "message": f"{self.device_id}: telemetry {json.dumps(payload, ensure_ascii=False)}",
                            "level": "info",
                        },
                    )
                except Exception as exc:
                    self._put_event(
                        "log", {"message": f"{self.device_id}: 送信エラー — {exc}", "level": "error"}
                    )
            self._stop_send.wait(interval)

    def _put_event(self, event_type: str, data: dict) -> None:
        self._queue.put((self.device_id, event_type, data))
```

- [ ] **Step 6: テストが全て PASS することを確認する**

```powershell
pytest tests/test_device_worker.py -v
```

期待: 6 passed

- [ ] **Step 7: コミットする**

```powershell
git add simulator/requirements.txt simulator/device_worker.py simulator/tests/
git commit -m "feat: add DeviceWorker and unit tests for device simulator"
```

---

### Task 2: Tkinter GUI (simulator.py)

**Files:**
- Create: `simulator/simulator.py`

**Interfaces:**
- Consumes (Task 1):
  - `DeviceWorker(device_id, api_url, broker_host, broker_port, bootstrap_token, cert_dir, event_queue)`
  - `worker.start()`, `worker.start_sending(interval: float, payload_fn: Callable[[], dict])`, `worker.stop_sending()`, `worker.stop()`
  - キューイベント: `(device_id: str, event_type: str, data: dict)`
- Produces: `python simulator/simulator.py` で起動できる GUI アプリ

- [ ] **Step 1: `simulator/simulator.py` を作成する**

```python
# simulator/simulator.py
import json
import os
import queue
import random
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, simpledialog, ttk
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device_worker import DeviceWorker


CERT_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")

STATE_ICONS = {
    "provisioning": "⚡",
    "connecting": "⟳",
    "connected": "●",
    "disconnected": "○",
    "error": "✗",
}
STATE_COLORS = {
    "provisioning": "orange",
    "connecting": "blue",
    "connected": "green",
    "disconnected": "gray",
    "error": "red",
}


class SimulatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IoT Platform デバイスシミュレーター")
        self.minsize(720, 520)

        self._event_queue: queue.Queue = queue.Queue()
        self._workers: dict[str, DeviceWorker] = {}
        self._state_labels: dict[str, tk.Label] = {}

        self._api_url = tk.StringVar(value="https://localhost/api")
        self._broker_host = tk.StringVar(value="localhost")
        self._broker_port = tk.IntVar(value=8883)
        self._bootstrap_token = tk.StringVar(value="")
        self._interval = tk.DoubleVar(value=5.0)
        self._use_random = tk.BooleanVar(value=True)
        self._custom_json_text: Optional[scrolledtext.ScrolledText] = None
        self._log_text: Optional[scrolledtext.ScrolledText] = None
        self._device_list_frame: Optional[tk.Frame] = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_queue)

    # ─── UI 構築 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        conn_frame = ttk.LabelFrame(self, text="接続設定", padding=6)
        conn_frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        ttk.Label(conn_frame, text="API URL:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(conn_frame, textvariable=self._api_url, width=35).grid(row=0, column=1, padx=4)
        ttk.Label(conn_frame, text="MQTTホスト:").grid(row=0, column=2, sticky=tk.W, padx=(8, 0))
        ttk.Entry(conn_frame, textvariable=self._broker_host, width=16).grid(row=0, column=3, padx=4)
        ttk.Label(conn_frame, text=":").grid(row=0, column=4)
        ttk.Spinbox(conn_frame, textvariable=self._broker_port, from_=1, to=65535, width=6).grid(row=0, column=5)

        ttk.Label(conn_frame, text="Bootstrap Token:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(conn_frame, textvariable=self._bootstrap_token, width=52, show="*").grid(
            row=1, column=1, columnspan=5, sticky=tk.W, padx=4
        )

        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        # 左: デバイス一覧
        left = ttk.LabelFrame(pane, text="デバイス一覧", padding=6)
        pane.add(left, minsize=200)

        self._device_list_frame = tk.Frame(left)
        self._device_list_frame.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(left)
        btn_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_frame, text="+ 追加", command=self._add_device_dialog).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="▶ 全開始", command=self._start_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="■ 全停止", command=self._stop_all).pack(side=tk.LEFT)

        # 右: テレメトリ設定
        right = ttk.LabelFrame(pane, text="テレメトリ設定", padding=6)
        pane.add(right, minsize=280)

        ttk.Radiobutton(
            right, text="ランダム値  (temperature / humidity / pressure)",
            variable=self._use_random, value=True,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            right, text="カスタム JSON",
            variable=self._use_random, value=False,
        ).pack(anchor=tk.W, pady=(2, 4))

        self._custom_json_text = scrolledtext.ScrolledText(right, height=6, width=32, font=("Consolas", 10))
        self._custom_json_text.insert("1.0", '{\n  "temperature": 25.0\n}')
        self._custom_json_text.pack(fill=tk.BOTH, expand=True)

        ivl_frame = tk.Frame(right)
        ivl_frame.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(ivl_frame, text="送信間隔:").pack(side=tk.LEFT)
        ttk.Spinbox(ivl_frame, textvariable=self._interval, from_=1, to=3600, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(ivl_frame, text="秒").pack(side=tk.LEFT)

        send_frame = tk.Frame(right)
        send_frame.pack(fill=tk.X)
        ttk.Button(send_frame, text="▶ 送信開始", command=self._start_sending).pack(side=tk.LEFT)
        ttk.Button(send_frame, text="■ 送信停止", command=self._stop_sending).pack(side=tk.LEFT, padx=4)

        log_outer = ttk.LabelFrame(self, text="ログ", padding=4)
        log_outer.pack(fill=tk.BOTH, padx=10, pady=(0, 10))

        self._log_text = scrolledtext.ScrolledText(
            log_outer, height=10, state=tk.DISABLED, font=("Consolas", 9)
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)
        self._log_text.tag_config("error", foreground="red")
        self._log_text.tag_config("warn", foreground="orange")
        self._log_text.tag_config("info", foreground="black")

    # ─── デバイス管理 ───────────────────────────────────────────────────────

    def _add_device_dialog(self) -> None:
        device_id = simpledialog.askstring("デバイス追加", "Device ID を入力してください:", parent=self)
        if not device_id or not device_id.strip():
            return
        device_id = device_id.strip()
        if device_id in self._workers:
            messagebox.showwarning("重複", f'"{device_id}" は既に追加されています', parent=self)
            return
        self._add_device(device_id)

    def _add_device(self, device_id: str) -> None:
        cert_dir = os.path.join(CERT_BASE, device_id)
        worker = DeviceWorker(
            device_id=device_id,
            api_url=self._api_url.get(),
            broker_host=self._broker_host.get(),
            broker_port=self._broker_port.get(),
            bootstrap_token=self._bootstrap_token.get(),
            cert_dir=cert_dir,
            event_queue=self._event_queue,
        )
        self._workers[device_id] = worker

        row = tk.Frame(self._device_list_frame)
        row.pack(fill=tk.X, pady=1, anchor=tk.W)
        state_lbl = tk.Label(row, text="⚡ provisioning", fg="orange", width=20, anchor=tk.W, font=("Consolas", 9))
        state_lbl.pack(side=tk.LEFT, padx=4)
        tk.Label(row, text=device_id, font=("Consolas", 9)).pack(side=tk.LEFT)
        self._state_labels[device_id] = state_lbl

        worker.start()

    # ─── 送信制御 ───────────────────────────────────────────────────────────

    def _get_payload_fn(self):
        if self._use_random.get():
            def fn():
                return {
                    "temperature": round(random.uniform(20.0, 30.0), 2),
                    "humidity": round(random.uniform(40.0, 80.0), 2),
                    "pressure": round(random.uniform(1000.0, 1020.0), 2),
                }
            return fn
        raw = self._custom_json_text.get("1.0", tk.END).strip()
        try:
            payload_dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._append_log(f"JSON パースエラー: {exc}", level="warn")
            return None
        return lambda: payload_dict

    def _start_sending(self) -> None:
        payload_fn = self._get_payload_fn()
        if payload_fn is None:
            return
        interval = self._interval.get()
        for worker in self._workers.values():
            worker.stop_sending()
            worker.start_sending(interval, payload_fn)

    def _stop_sending(self) -> None:
        for worker in self._workers.values():
            worker.stop_sending()

    def _start_all(self) -> None:
        self._start_sending()

    def _stop_all(self) -> None:
        self._stop_sending()

    # ─── キュー処理・UI 更新 ────────────────────────────────────────────────

    def _process_queue(self) -> None:
        try:
            while True:
                device_id, event_type, data = self._event_queue.get_nowait()
                if event_type == "status":
                    self._update_state(device_id, data["state"])
                elif event_type == "log":
                    self._append_log(data["message"], level=data.get("level", "info"))
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    def _update_state(self, device_id: str, state: str) -> None:
        lbl = self._state_labels.get(device_id)
        if lbl:
            icon = STATE_ICONS.get(state, "?")
            color = STATE_COLORS.get(state, "black")
            lbl.config(text=f"{icon} {state}", fg=color)

    def _append_log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{ts}] {message}\n", level)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ─── 終了処理 ───────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        for worker in self._workers.values():
            worker.stop()
        self.destroy()


if __name__ == "__main__":
    app = SimulatorApp()
    app.mainloop()
```

- [ ] **Step 2: 手動テスト — 起動確認**

```powershell
cd C:\...\iot-platform
pip install -e sdk/python
pip install paho-mqtt==1.6.1 requests
python simulator/simulator.py
```

期待: GUI ウィンドウが開く。「接続設定」「デバイス一覧」「テレメトリ設定」「ログ」の各エリアが表示されている。

- [ ] **Step 3: 手動テスト — デバイス追加と接続**

1. Admin UI でテナントのプロビジョニングトークンを発行する
2. Bootstrap Token フィールドに貼り付ける
3. 「+ 追加」をクリックし Device ID に `sim-001` と入力する
4. 状態が `⚡ provisioning` → `⟳ connecting` → `● connected` に変わることを確認する
5. ログに「プロビジョニング完了」「接続完了」が表示されることを確認する
6. Admin UI の「デバイス」タブで `sim-001` が `connection_status = online` になっていることを確認する

- [ ] **Step 4: 手動テスト — テレメトリ送信（ランダムとカスタム）**

1. ランダム値モードで間隔 5 秒、「▶ 送信開始」をクリックする
2. ログに `sim-001: telemetry {"temperature": ..., "humidity": ..., "pressure": ...}` が 5 秒ごとに流れることを確認する
3. Grafana ダッシュボードでグラフにデータが届いていることを確認する
4. 「■ 送信停止」→ カスタム JSON モードに切り替え → テキストエリアに `{"co2": 400, "noise": 55}` を入力 → 「▶ 送信開始」
5. ログにカスタムフィールドが表示されることを確認する

- [ ] **Step 5: 手動テスト — 再起動後の証明書再利用**

1. ウィンドウを閉じる（ログに「切断」が流れることを確認）
2. `simulator/certs/sim-001/` フォルダに cert.pem / key.pem / ca.pem / device_id の 4 ファイルが残っていることを確認する
3. シミュレーターを再起動し、`sim-001` を再追加する
4. ログに「証明書を再利用して接続中」と表示され、`provisioning` ステップをスキップして接続することを確認する

- [ ] **Step 6: 手動テスト — 複数デバイス同時接続**

1. `sim-001`, `sim-002`, `sim-003` の 3 台を追加する
2. 全台が `● connected` になることを確認する
3. 「▶ 全開始」でテレメトリを送信し、3 台分のログが流れることを確認する
4. Admin UI のデバイスタブで 3 台とも `online` であることを確認する

- [ ] **Step 7: コミットする**

```powershell
git add simulator/simulator.py
git commit -m "feat: add Tkinter GUI for device simulator"
```
