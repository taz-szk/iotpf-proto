# Windows デバイスシミュレーター 設計ドキュメント

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** IoT Platform に対してデバイスの登録（プロビジョニング）とテレメトリデータ送信を行う Windows 向け GUI シミュレーターを作成する。

**Architecture:** 既存の `sdk/python/iot_platform` ライブラリ（IotClient）をバックエンドとして流用し、Tkinter GUI でラップする。デバイス 1 台につき 1 バックグラウンドスレッドを割り当て、`queue.Queue` 経由でスレッドセーフに UI を更新する。証明書は `simulator/certs/{device_id}/` に永続化し、再起動後は provisioning をスキップして直接再接続できる。

**Tech Stack:** Python 3.8+, Tkinter (標準ライブラリ), paho-mqtt 1.6.1, requests, ssl (標準ライブラリ)

## Global Constraints

- Python 3.8 以上、Windows 10/11 で動作すること（追加ランタイム不要）
- 外部依存は `paho-mqtt==1.6.1` と `requests` のみ（既存 SDK の依存と同じ）
- 既存 `sdk/python/iot_platform` パッケージを `pip install -e sdk/python` でインストールして import する
- 証明書保存先: `simulator/certs/{device_id}/` — `cert.pem`, `key.pem`, `ca.pem`, `device_id` の 4 ファイル
- デバイスの MQTT client_id および username: `{tenant_id}:{device_id}`（空パスワード）
- MQTT ブローカーポート: 8883（mTLS 必須）
- テレメトリトピック: `/{tenant_id}/devices/{device_id}/telemetry`
- ステータストピック: `/{tenant_id}/devices/{device_id}/status`
- スレッド間通信は `queue.Queue` + Tkinter `after(100, ...)` ポーリングのみ（直接 UI 操作禁止）

---

## ファイル構成

```
simulator/
  ├── simulator.py       # Tkinter GUI メインウィンドウ・SimulatorApp クラス
  ├── device_worker.py   # DeviceWorker クラス（スレッド per デバイス）
  └── requirements.txt   # paho-mqtt==1.6.1, requests
```

証明書は実行時に自動生成:
```
simulator/certs/
  └── {device_id}/
        ├── cert.pem
        ├── key.pem
        ├── ca.pem
        └── device_id   # テナントIDを記録
```

---

## クラス設計

### `DeviceWorker` (`device_worker.py`)

バックグラウンドスレッドとして動作するデバイス 1 台分のロジック。

```python
class DeviceWorker(threading.Thread):
    def __init__(self, device_id: str, api_url: str, broker_host: str,
                 bootstrap_token: str, cert_dir: str, event_queue: queue.Queue):
        ...

    def run(self) -> None:
        # 1. certs/{device_id}/ が存在すれば load_credentials、なければ provision
        # 2. IotClient.connect() で MQTT 接続
        # 3. _running が True の間、_send_event() でキューにイベントを積む

    def start_sending(self, interval: float, payload_fn: Callable[[], dict]) -> None:
        # 送信ループを開始する（別スレッドまたは内部タイマー）

    def stop_sending(self) -> None:
        # 送信ループを停止

    def stop(self) -> None:
        # MQTT disconnect + スレッド終了

    def _send_event(self, event_type: str, data: dict) -> None:
        # event_queue.put((self.device_id, event_type, data))
```

**キューに積むイベント種別:**

| event_type | data キー | 意味 |
|------------|-----------|------|
| `"status"` | `"state"`: `"provisioning"` / `"connecting"` / `"connected"` / `"disconnected"` / `"error"` | 接続状態変化 |
| `"telemetry"` | `"payload"`: dict | テレメトリ送信成功 |
| `"log"` | `"message"`: str, `"level"`: `"info"` / `"error"` | ログメッセージ |

### `SimulatorApp` (`simulator.py`)

Tkinter メインウィンドウ。

```python
class SimulatorApp(tk.Tk):
    def __init__(self):
        # 設定変数（StringVar）: api_url, broker_host, bootstrap_token
        # workers: dict[device_id, DeviceWorker]
        # event_queue: queue.Queue
        # after(100, self._process_queue) でポーリング開始

    def _build_ui(self) -> None: ...
    def _add_device_dialog(self) -> None: ...   # device_id 入力ダイアログ
    def _add_device(self, device_id: str) -> None: ...  # DeviceWorker 生成 + リスト行追加
    def _start_all(self) -> None: ...
    def _stop_all(self) -> None: ...
    def _process_queue(self) -> None: ...       # イベントキューを消化して UI 更新
    def _on_close(self) -> None: ...            # 全 worker を stop() してから終了
```

### UI レイアウト

```
┌──────────────────────────────────────────────────────────────┐
│  IoT Platform デバイスシミュレーター                            │
├──────────────────────────────────────────────────────────────┤
│  API URL: [https://localhost/api    ]  MQTT: [localhost:8883] │
│  Bootstrap Token: [________________________________]          │
├──────────────────┬───────────────────────────────────────────┤
│ デバイス一覧 (LabelFrame)  │ テレメトリ設定 (LabelFrame)       │
│ ┌──────────────────────┐  │  ◉ ランダム値 (temp/hum/pres)    │
│ │ ● sim-001  接続中    │  │  ○ カスタムJSON                  │
│ │ ○ sim-002  切断      │  │  ┌──────────────────────────┐   │
│ │ ⚡ sim-003  登録中   │  │  │ {"temperature": 25.0}    │   │
│ └──────────────────────┘  │  └──────────────────────────┘   │
│ [+ デバイス追加]           │  送信間隔: [5] 秒               │
│ [▶ 全開始] [■ 全停止]      │  [▶ 送信開始] [■ 送信停止]      │
├──────────────────┴───────────────────────────────────────────┤
│ ログ (ScrolledText, readonly)                                 │
│ [12:00:05] sim-001: 接続完了                                  │
│ [12:00:10] sim-001: telemetry {"temperature": 23.4, ...}     │
└──────────────────────────────────────────────────────────────┘
```

**デバイス一覧の行:** 1 デバイス = `ttk.Frame` 1 行（device_id ラベル + 状態インジケーター）

---

## 動作フロー

### 初回デバイス追加と接続

1. 「+ デバイス追加」ボタン → device_id 入力ダイアログ
2. `DeviceWorker` を生成して `.start()`
3. Worker が `simulator/certs/{device_id}/` を確認
   - 存在しない → `IotClient.provision(bootstrap_token, device_id, cert_dir)` を実行
   - 存在する → `IotClient.load_credentials(cert_dir)` で再利用
4. `IotClient.connect()` で MQTT 接続（mTLS、ポート 8883）
5. 接続後 `status: online` を自動 publish
6. キューに `("status", {"state": "connected"})` を積む → UI の状態インジケーターが緑になる

### テレメトリ送信

1. 「送信開始」ボタン → 全 worker に `start_sending(interval, payload_fn)` を呼ぶ
2. `payload_fn` は選択モードに応じて以下を返す:
   - ランダムモード: `{"temperature": uniform(20,30), "humidity": uniform(40,80), "pressure": uniform(1000,1020)}`
   - カスタム JSON モード: テキストエリアの JSON を `json.loads()` してそのまま返す（パースエラー時はログに表示してスキップ）
3. Worker 内の送信スレッドが `IotClient.publish_telemetry(payload)` を呼ぶ
4. 送信成功ごとに `("telemetry", {"payload": ...})` をキューに積む

### エラー処理

| エラー | UI表示 |
|--------|--------|
| provisioning 失敗 (401/403/503) | 状態「エラー」、ログに赤字でHTTPステータスとメッセージを表示 |
| MQTT 接続タイムアウト (30s) | 状態「エラー」、ログに赤字 |
| カスタム JSON パースエラー | 送信スキップ、ログに黄色で警告 |
| MQTT 切断（予期せぬ） | 状態「切断」、ログに表示。再接続は手動（再度「全開始」） |

---

## 起動方法

```powershell
# 初回セットアップ
cd C:\...\iot-platform
pip install -e sdk/python
pip install paho-mqtt==1.6.1 requests

# 起動
python simulator/simulator.py
```

---

## テスト方法

1. Admin UI でプロビジョニングトークンを発行
2. シミュレーターに API URL・MQTT ホスト・トークンを入力
3. デバイスを 3 台追加し接続確認（Admin UI の「デバイス」タブで `connection_status=online` を確認）
4. 送信開始 → Grafana ダッシュボードにテレメトリが届くことを確認
5. シミュレーターを閉じる → デバイスが `offline` になることを確認
6. 再起動して同じ device_id で再接続 → 証明書を再利用してプロビジョニングなしで接続されることを確認
