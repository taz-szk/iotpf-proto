# Simulator OTA Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** シミュレーターがMQTT経由でOTAコマンドを受け取り、実際にファームウェアをダウンロード・SHA256検証して、デバイスごとの進捗ダイアログにリアルタイム表示する。

**Architecture:** `DeviceWorker` が `IotClient.on_command()` でコマンドを受け取り、キュー経由で `SimulatorApp` に通知する。`SimulatorApp` がデバイスごとに `OtaProgressDialog` を開き、バックグラウンドスレッドがダウンロードと検証を行いながら `self.after(0, ...)` でUIをスレッドセーフに更新する。OTA完了後、`DeviceWorker.fw_version` が更新され以降のテレメトリに新バージョンが含まれる。

**Tech Stack:** Python 3.x, Tkinter (ttk), requests (streaming), hashlib (SHA256), threading, queue

## Global Constraints

- Tkinter UIの更新はすべてメインスレッドから行う。バックグラウンドスレッドは `self.after(0, callback)` のみ使う
- `queue.Queue.put()` はスレッドセーフなので直接呼んでよい
- テストは `simulator/tests/` に置き、`unittest` + `unittest.mock.patch` で書く
- テスト実行は `simulator/` ディレクトリで `python -m pytest tests/ -v` を使う
- SSL検証フラグ (`ssl_verify`) はテナント設定から必ず引き継ぐ（自己署名証明書対応）
- `fw_version` の初期値は `"1.0.0"` とする
- 既存コードのスタイル: `tk.Label`, `ttk.Button`, `ttk.Frame` を使い分けており、メッセージはすべて日本語

---

## File Map

| ファイル | 変更内容 |
|---|---|
| `simulator/device_worker.py` | `fw_version` 属性追加、`on_command` 登録、`_handle_command` メソッド追加、テレメトリへの `fw_version` 注入 |
| `simulator/simulator.py` | `OtaProgressDialog` クラス追加 (新規)、`SimulatorApp` にダイアログ管理・fw_versionラベル・イベントハンドリング追加 |
| `simulator/tests/test_device_worker.py` | OTA関連テスト追加 |

---

## Task 1: DeviceWorker OTA support

**Files:**
- Modify: `simulator/device_worker.py`
- Test: `simulator/tests/test_device_worker.py`

**Interfaces:**
- Produces:
  - `DeviceWorker.fw_version: str` — 現在のファームウェアバージョン（外部から直接書き換え可）
  - イベント `(wid, "ota_start", {"device_id": str, "payload": dict, "ssl_verify": bool})` — OTAコマンド受信時にキューへ
  - テレメトリに `"fw_version": str` が含まれる

- [ ] **Step 1: テストを書く（失敗確認用）**

`simulator/tests/test_device_worker.py` の末尾に以下を追加する（既存 `import` はそのまま）:

```python
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
```

- [ ] **Step 2: テストが失敗することを確認する**

```
cd C:\Users\USER\AI-Work\iot-platform\simulator
python -m pytest tests/test_device_worker.py::TestDeviceWorkerOta -v
```

期待: `AttributeError: 'DeviceWorker' object has no attribute 'fw_version'` などで FAIL

- [ ] **Step 3: `device_worker.py` を実装する**

`DeviceWorker.__init__` の `self._send_thread` 行の直後に追加:
```python
        self.fw_version: str = "1.0.0"
```

`run()` メソッド内、`self._put_event("status", {"state": "connected"})` の直後に追加:
```python
            self._client.on_command(self._handle_command)
```

`_send_loop` 内の `payload = payload_fn()` の直後、`self._client.publish_telemetry(payload)` の直前を変更:
```python
                    payload = payload_fn()
                    payload["fw_version"] = self.fw_version
                    self._client.publish_telemetry(payload)
```

ファイル末尾（`_put_event` の後）に `_handle_command` を追加:
```python
    def _handle_command(self, cmd_type: str, payload: dict) -> None:
        if cmd_type == "ota":
            self._put_event("ota_start", {
                "device_id": self.device_id,
                "payload": payload,
                "ssl_verify": self._ssl_verify,
            })
        else:
            self._put_event("log", {
                "message": f"{self.device_id}: 未知のコマンド — {cmd_type}",
                "level": "warn",
            })
```

- [ ] **Step 4: テストが通ることを確認する**

```
cd C:\Users\USER\AI-Work\iot-platform\simulator
python -m pytest tests/test_device_worker.py -v
```

期待: 全テスト PASS（既存テストも含む）

- [ ] **Step 5: コミット**

```
git add simulator/device_worker.py simulator/tests/test_device_worker.py
git commit -m "feat(simulator): add OTA command handling and fw_version telemetry injection"
```

---

## Task 2: OtaProgressDialog クラス

**Files:**
- Modify: `simulator/simulator.py`（`SimulatorApp` クラスの直前に新クラスを追加）

**Interfaces:**
- Consumes: `queue.Queue` (Task 1 で定義したイベントキュー)
- Produces:
  - `OtaProgressDialog(parent, wid, device_id, old_version, new_version, payload, ssl_verify, event_queue, out_path)` — コンストラクタ
  - `OtaProgressDialog.start_ota()` — バックグラウンドOTAスレッド開始
  - イベント `(wid, "ota_done", {"version": str})` — OTA成功時にキューへ
  - イベント `(wid, "ota_failed", {"error": str})` — OTA失敗時にキューへ

**Testing:** OtaProgressDialogはTkinterウィジェットであり自動テストは困難。Step 4でスクリプトを使って手動検証する。

- [ ] **Step 1: `simulator.py` の import を更新する**

ファイル先頭の import ブロック（`import sys` の行付近）に以下を追加（既存importと重複しないよう確認）:

```python
import hashlib
import threading
```

`from datetime import datetime` はすでにある。`import requests` は**不要**（`simulator.py` 内の `_run_ota` で `import requests` をローカルでインポートする。理由: Tkinterアプリ起動時のインポートエラーを避けるため）。

- [ ] **Step 2: `OtaProgressDialog` クラスを追加する**

`simulator.py` の `# ─── メインアプリ ───` コメント行の直前（`class SimulatorApp` の直前）に以下のクラスを丸ごと挿入する:

```python
# ─── OTA 進捗ダイアログ ───────────────────────────────────────────────────────

class OtaProgressDialog(tk.Toplevel):
    """デバイスごとのOTA進捗ウィンドウ（非モーダル）"""

    def __init__(
        self,
        parent: tk.Widget,
        wid: int,
        device_id: str,
        old_version: str,
        new_version: str,
        payload: dict,
        ssl_verify: bool,
        event_queue: queue.Queue,
        out_path: str,
    ):
        super().__init__(parent)
        self._wid = wid
        self._device_id = device_id
        self._old_version = old_version
        self._new_version = new_version
        self._payload = payload
        self._ssl_verify = ssl_verify
        self._event_queue = event_queue
        self._out_path = out_path
        self._done = False

        self.title(f"OTA - {device_id}")
        self.resizable(True, True)
        self.minsize(520, 400)

        # ヘッダー
        hf = ttk.LabelFrame(self, text="ファームウェア更新", padding=6)
        hf.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(hf, text=f"v{old_version}  →  v{new_version}",
                 font=("", 12, "bold")).pack(anchor=tk.W)

        # プログレスバー
        pf = ttk.LabelFrame(self, text="ダウンロード進捗", padding=6)
        pf.pack(fill=tk.X, padx=8, pady=4)
        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress_bar = ttk.Progressbar(
            pf, variable=self._progress_var, maximum=100.0, length=480)
        self._progress_bar.pack(fill=tk.X, pady=(0, 4))
        self._progress_label = tk.Label(pf, text="待機中...", anchor=tk.W, font=("Consolas", 9))
        self._progress_label.pack(anchor=tk.W)

        # ログ
        lf = ttk.LabelFrame(self, text="ログ", padding=4)
        lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self._log_text = scrolledtext.ScrolledText(
            lf, height=12, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # ステータス + 閉じるボタン
        bf = tk.Frame(self)
        bf.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._status_label = tk.Label(bf, text="● OTA処理中...", fg="blue", anchor=tk.W)
        self._status_label.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self._close_btn = ttk.Button(bf, text="閉じる", state=tk.DISABLED,
                                     command=self.destroy)
        self._close_btn.pack(side=tk.RIGHT)

        _center_on_parent(self, parent)

    def start_ota(self) -> None:
        t = threading.Thread(target=self._run_ota, daemon=True,
                             name=f"ota-{self._device_id}")
        t.start()

    # ─── バックグラウンドスレッド ─────────────────────────────────────────────

    def _run_ota(self) -> None:
        try:
            import requests as _req
            self._schedule_log(
                f"OTAコマンド受信 (firmware_id: {self._payload.get('firmware_id', 'unknown')})")
            url = self._payload["download_url"]
            checksum_raw = self._payload.get("checksum", "")
            expected_sha256 = checksum_raw.removeprefix("sha256:")
            file_size = self._payload.get("file_size", 0)

            # ダウンロード開始
            size_str = f"{file_size / 1024 / 1024:.1f} MB" if file_size else "不明"
            self._schedule_log(f"ダウンロード開始: {size_str}")
            self._schedule_log(f"URL: {url}")

            resp = _req.get(url, stream=True, verify=self._ssl_verify, timeout=300)
            resp.raise_for_status()

            total = int(resp.headers.get("Content-Length", file_size or 0))

            sha256 = hashlib.sha256()
            downloaded = 0
            os.makedirs(os.path.dirname(self._out_path), exist_ok=True)

            with open(self._out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha256.update(chunk)
                    downloaded += len(chunk)
                    self.after(0, self._update_progress, downloaded, total)

            self._schedule_log(f"ダウンロード完了 ({downloaded / 1024 / 1024:.2f} MB)")

            # SHA256 検証
            actual_sha256 = sha256.hexdigest()
            self._schedule_log("SHA256 検証中...")
            self._schedule_log(f"  期待値: {expected_sha256[:32]}...")
            self._schedule_log(f"  計算値: {actual_sha256[:32]}...")

            if actual_sha256 == expected_sha256:
                self._schedule_log("✓ チェックサム一致")
                self._schedule_log(
                    f"バージョン更新: {self._old_version} → {self._new_version}")
                self._event_queue.put(
                    (self._wid, "ota_done", {"version": self._new_version}))
                self.after(0, self._finish, True, f"✓ OTA完了 (v{self._new_version})")
            else:
                self._schedule_log(
                    f"✗ チェックサム不一致\n  期待: {expected_sha256}\n  実際: {actual_sha256}")
                self._event_queue.put(
                    (self._wid, "ota_failed", {"error": "checksum_mismatch"}))
                self.after(0, self._finish, False, "✗ チェックサム不一致")

        except Exception as exc:
            self._schedule_log(f"✗ OTAエラー: {exc}")
            self._event_queue.put(
                (self._wid, "ota_failed", {"error": str(exc)}))
            self.after(0, self._finish, False, f"✗ エラー: {exc}")

    # ─── UIコールバック（メインスレッドから呼ばれる）──────────────────────────

    def _update_progress(self, downloaded: int, total: int) -> None:
        try:
            if total > 0:
                pct = downloaded / total * 100
                self._progress_var.set(pct)
                d_mb = downloaded / 1024 / 1024
                t_mb = total / 1024 / 1024
                self._progress_label.config(
                    text=f"{d_mb:.1f} MB / {t_mb:.1f} MB  ({pct:.0f}%)")
            else:
                d_mb = downloaded / 1024 / 1024
                self._progress_label.config(text=f"{d_mb:.1f} MB ダウンロード中...")
        except tk.TclError:
            pass

    def _finish(self, success: bool, message: str) -> None:
        try:
            self._done = True
            self._status_label.config(
                text=message, fg="green" if success else "red")
            self._progress_var.set(100.0 if success else self._progress_var.get())
            self._close_btn.config(state=tk.NORMAL)
        except tk.TclError:
            pass

    def _schedule_log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _append_log(self, msg: str) -> None:
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_text.config(state=tk.NORMAL)
            self._log_text.insert(tk.END, f"[{ts}] {msg}\n")
            self._log_text.see(tk.END)
            self._log_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass
```

- [ ] **Step 3: 構文エラーがないことを確認する**

```
cd C:\Users\USER\AI-Work\iot-platform\simulator
python -c "import simulator; print('OK')"
```

期待: `OK` が表示される

- [ ] **Step 4: ダイアログの手動動作確認**

以下のスクリプトを一時ファイルとして作成・実行してダイアログの見た目と動作を確認する:

```python
# test_ota_dialog.py (simulator/ に一時作成、確認後削除)
import queue, tkinter as tk, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulator import OtaProgressDialog

root = tk.Tk()
root.title("テスト")
root.geometry("200x100")
q = queue.Queue()

# ダミーOTAコマンドペイロード（実際のURLが必要な場合は差し替え）
payload = {
    "firmware_id": "test-fw",
    "version": "2.0.0",
    "download_url": "https://httpbin.org/bytes/102400",  # 100KB のダミー
    "checksum": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "file_size": 102400,
}

def open_dialog():
    dlg = OtaProgressDialog(
        parent=root, wid=0, device_id="test-001",
        old_version="1.0.0", new_version="2.0.0",
        payload=payload, ssl_verify=True,
        event_queue=q,
        out_path=os.path.join(os.path.dirname(__file__), "certs", "test-fw.bin"),
    )
    dlg.start_ota()

tk.Button(root, text="OTA開始", command=open_dialog).pack(pady=20)
root.mainloop()
```

確認点:
- ダイアログが親ウィンドウ中央に表示される
- プログレスバーが増加する
- ログに「OTAコマンド受信」「ダウンロード開始」「SHA256 検証中」等が表示される
- チェックサム不一致で「✗」が赤表示され「閉じる」ボタンが有効になる
- ダイアログを閉じても親ウィンドウが動作し続ける（非モーダル確認）

- [ ] **Step 5: テスト用ファイルを削除してコミット**

```
del simulator\test_ota_dialog.py  # Windowsの場合
git add simulator/simulator.py
git commit -m "feat(simulator): add OtaProgressDialog with streaming download and SHA256 verification"
```

---

## Task 3: SimulatorApp OTA wiring

**Files:**
- Modify: `simulator/simulator.py`（`SimulatorApp` クラス内）

**Interfaces:**
- Consumes: `OtaProgressDialog` (Task 2)、`DeviceWorker.fw_version` (Task 1)、イベント `"ota_start"`, `"ota_done"`, `"ota_failed"` (Task 1)

- [ ] **Step 1: `SimulatorApp.__init__` にOTA管理用dictを追加する**

`SimulatorApp.__init__` 内の `self._field_rows: list[dict] = []` 行の直後に追加:

```python
        self._ota_dialogs: dict[int, OtaProgressDialog] = {}
        self._fw_version_labels: dict[int, tk.Label] = {}
```

- [ ] **Step 2: `_add_device` でデバイス行に `fw_version` ラベルを追加する**

`_add_device` メソッド内、デバイス行を構築している部分を探す。現在のコード:

```python
        state_lbl = tk.Label(row, text="⚡ provisioning", fg="orange",
                             width=20, anchor=tk.W, font=("Consolas", 9))
        state_lbl.pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=device_id, font=("Consolas", 9)).pack(side=tk.LEFT)
        tk.Label(row, text=f" [{tenant['name']}]", fg="gray", font=("", 8)).pack(side=tk.LEFT)
        tk.Button(row, text="✕", font=("", 8), fg="gray", relief=tk.FLAT, bd=0,
                  cursor="hand2", command=lambda w=wid: self._remove_device(w)
                  ).pack(side=tk.RIGHT, padx=2)
        self._state_labels[wid] = state_lbl
        self._device_rows[wid] = row
```

これを以下に置き換える:

```python
        state_lbl = tk.Label(row, text="⚡ provisioning", fg="orange",
                             width=20, anchor=tk.W, font=("Consolas", 9))
        state_lbl.pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=device_id, font=("Consolas", 9)).pack(side=tk.LEFT)
        tk.Label(row, text=f" [{tenant['name']}]", fg="gray", font=("", 8)).pack(side=tk.LEFT)
        fw_lbl = tk.Label(row, text="fw: 1.0.0", fg="gray", font=("Consolas", 8))
        fw_lbl.pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(row, text="✕", font=("", 8), fg="gray", relief=tk.FLAT, bd=0,
                  cursor="hand2", command=lambda w=wid: self._remove_device(w)
                  ).pack(side=tk.RIGHT, padx=2)
        self._state_labels[wid] = state_lbl
        self._fw_version_labels[wid] = fw_lbl
        self._device_rows[wid] = row
```

- [ ] **Step 3: `_remove_device` でOTAダイアログを閉じる**

`_remove_device` メソッド内、`worker.stop()` の直前に追加:

```python
        # OTA ダイアログが開いていれば閉じる
        dlg = self._ota_dialogs.pop(wid, None)
        if dlg:
            try:
                dlg.destroy()
            except tk.TclError:
                pass
```

また、`self._state_labels.pop(wid, None)` の行の後に追加:

```python
        self._fw_version_labels.pop(wid, None)
```

- [ ] **Step 4: `_process_queue` に OTA イベントハンドラを追加する**

`_process_queue` 内の `elif event_type == "telemetry":` ブロックの直後に追加:

```python
                elif event_type == "ota_start":
                    if wid in self._ota_dialogs:
                        self._append_log(
                            f"{data['device_id']}: OTA既に進行中 — スキップ", level="warn")
                    else:
                        worker = self._workers.get(wid)
                        old_ver = worker.fw_version if worker else "unknown"
                        new_ver = data["payload"].get("version", "unknown")
                        tenant_name = self._device_tenants.get(wid, "unknown")
                        out_path = os.path.join(
                            CERT_BASE, tenant_name, data["device_id"], "ota_firmware.bin")
                        dlg = OtaProgressDialog(
                            parent=self,
                            wid=wid,
                            device_id=data["device_id"],
                            old_version=old_ver,
                            new_version=new_ver,
                            payload=data["payload"],
                            ssl_verify=data["ssl_verify"],
                            event_queue=self._event_queue,
                            out_path=out_path,
                        )
                        self._ota_dialogs[wid] = dlg
                        dlg.protocol("WM_DELETE_WINDOW",
                                     lambda w=wid, d=dlg: self._on_ota_close(w, d))
                        dlg.start_ota()
                        self._append_log(
                            f"{data['device_id']}: OTA開始 v{old_ver} → v{new_ver}", level="info")

                elif event_type == "ota_done":
                    new_ver = data["version"]
                    worker = self._workers.get(wid)
                    if worker:
                        worker.fw_version = new_ver
                    lbl = self._fw_version_labels.get(wid)
                    if lbl:
                        lbl.config(text=f"fw: {new_ver}", fg="blue")
                    self._ota_dialogs.pop(wid, None)
                    dev_id = worker.device_id if worker else str(wid)
                    self._append_log(f"{dev_id}: OTA完了 → v{new_ver}", level="info")

                elif event_type == "ota_failed":
                    self._ota_dialogs.pop(wid, None)
                    worker = self._workers.get(wid)
                    dev_id = worker.device_id if worker else str(wid)
                    self._append_log(
                        f"{dev_id}: OTA失敗 — {data.get('error', '不明')}", level="error")
```

- [ ] **Step 5: `_on_ota_close` ヘルパーメソッドを追加する**

`_process_queue` メソッドの直後（`_update_state` の前）に追加:

```python
    def _on_ota_close(self, wid: int, dlg: "OtaProgressDialog") -> None:
        self._ota_dialogs.pop(wid, None)
        try:
            dlg.destroy()
        except tk.TclError:
            pass
```

- [ ] **Step 6: 構文確認**

```
cd C:\Users\USER\AI-Work\iot-platform\simulator
python -c "import simulator; print('OK')"
```

期待: `OK`

- [ ] **Step 7: 既存テストが通ることを確認する**

```
cd C:\Users\USER\AI-Work\iot-platform\simulator
python -m pytest tests/ -v
```

期待: 全テスト PASS

- [ ] **Step 8: エンドツーエンド手動テスト**

前提: iot-platform Docker環境が起動済み、シミュレーターでデバイスが接続済み

1. シミュレーターを起動してデバイスを接続（テナントを選んで「+ 追加」→接続確認）
2. デバイス行に `fw: 1.0.0` ラベルが表示されることを確認
3. 管理UI (`https://iot.example.com/admin/tenant-portal.html`) でファームウェアをアップロード
   - 任意のダミーファイルを用意: `echo "dummy firmware content" > dummy_fw.bin`
   - ファームウェア管理でアップロード（バージョン: `2.0.0`）
4. デバイスにOTAを配信
5. シミュレーターにOTAダイアログが開くことを確認
6. プログレスバーとログの内容を確認（SHA256値が両方表示されること）
7. チェックサム一致/不一致のどちらになったか確認（ダミーファイルの場合は不一致になる）
8. OTA完了時（一致の場合）: デバイス行の `fw: x.y.z` が更新され、青色になることを確認
9. テレメトリ送信中の場合: Grafanaダッシュボードで `fw_version` が更新されることを確認

- [ ] **Step 9: コミット**

```
git add simulator/simulator.py
git commit -m "feat(simulator): wire OTA dialogs into SimulatorApp with per-device fw_version tracking"
```
