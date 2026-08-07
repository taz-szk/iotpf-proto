# IoT Platform Python SDK

デバイスを IoT プラットフォームへ接続するための Python SDK です。

## 必要環境

- Python 3.11 以上
- paho-mqtt 1.6.x
- requests 2.28 以上

## インストール

```bash
pip install -e .
```

## クイックスタート

### 1. 初回プロビジョニング（Admin UI でトークン取得後）

```bash
BOOTSTRAP_TOKEN=<token> DEVICE_ID=my-sensor-001 python examples/01_provision.py
```

証明書が `./certs/` に保存されます。

### 2. テレメトリ送信

```bash
MQTT_BROKER_HOST=<broker> python examples/02_telemetry.py
```

### 3. OTA アップデート待機

```bash
MQTT_BROKER_HOST=<broker> python examples/03_ota_update.py
```

## API リファレンス

### `IotClient`

```python
client = IotClient(api_url, broker_host, broker_port=8883)

# プロビジョニング（初回のみ）
client.provision(bootstrap_token, device_id, cert_dir)

# 既存証明書で初期化
client.load_credentials(cert_dir)

# 接続（mTLS）
client.connect()                      # loop_start() 含む、online status を publish

# データ送信
client.publish_telemetry({"temperature": 25.3})
client.publish_status("online")

# コマンド受信
client.on_command(lambda cmd_type, payload: ...)

# ループ制御
client.loop_forever()                 # ブロッキング
client.disconnect()
```

### `OtaHandler`

```python
# OTA コマンドペイロードをそのまま渡す
OtaHandler.handle(payload, output_path)   # -> bool

# URL と SHA256 を直接指定
OtaHandler.download_and_verify(url, output_path, "sha256:abcdef...")  # -> bool
```

## 環境変数

| 変数 | 説明 | デフォルト |
|------|------|-----------|
| `IOT_API_URL` | Core API URL | `https://localhost/api` |
| `MQTT_BROKER_HOST` | MQTT ブローカーホスト | `localhost` |
| `CERT_DIR` | 証明書ディレクトリ | `./certs` |
| `BOOTSTRAP_TOKEN` | プロビジョニングトークン | （必須） |
| `DEVICE_ID` | デバイス ID | `my-sensor-001` |

## テスト実行

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
