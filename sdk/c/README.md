# IoT Platform C SDK

Linux デバイス向け C SDK（paho-mqtt / libcurl / OpenSSL）と  
MCU ポーティング向け抽象インターフェース（`iot_platform.h`）を提供します。

## 必要環境（Linux）

```bash
# Debian/Ubuntu
apt-get install -y \
    libpaho-mqtt-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    libcjson-dev \
    cmake build-essential
```

## ビルド

```bash
cd sdk/c
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j4
```

または CMake なし:

```bash
cd sdk/c
make
```

## テスト実行

```bash
cd sdk/c/build
ctest --output-on-failure
```

Expected: `compile_link_check` PASS

## クイックスタート

### 1. プロビジョニング

```bash
export IOT_API_URL=https://your-platform/api
export BOOTSTRAP_TOKEN=<token_from_admin_ui>
export DEVICE_ID=my-sensor-001
export CERT_DIR=./certs
export MQTT_BROKER=your-platform-host

./build/ota_update   # BOOTSTRAP_TOKEN があれば自動プロビジョニング
```

証明書が `$CERT_DIR/` に保存されます。

### 2. テレメトリ送信

```bash
./build/basic_telemetry
```

5 秒ごとにダミーセンサー値を publish します。

### 3. OTA 待機

```bash
unset BOOTSTRAP_TOKEN  # 既存証明書を使用
./build/ota_update
```

Admin UI でファームウェアアップロード後に「OTA 配信」を押すとコマンドが届きます。

## API リファレンス

```c
/* ライフサイクル */
iot_client_t *iot_client_create(api_url, broker_host, broker_port);
void iot_client_destroy(client);

/* プロビジョニング */
int iot_provision(client, bootstrap_token, device_id, cert_dir);  /* → 0 / IOT_ERR_* */
int iot_load_credentials(client, cert_dir);

/* TLS証明書検証は常に有効。api_url が自己署名/プライベートCA(例: ローカル
 * step-ca環境)の証明書を提示する場合のみ、iot_provision() より前に呼んで
 * そのCAのルート証明書(PEM)を指定すること。未呼び出し/NULLの場合はシステム
 * デフォルトのCAバンドルで検証する(Let's Encrypt等の公開CAならこれでよい) */
int iot_client_set_ca_cert_path(client, ca_cert_path);

/* 接続 */
int  iot_connect(client);    /* mTLS, subscribes commands topic, publishes "online" */
void iot_disconnect(client); /* publishes "offline", then disconnects */

/* 送信 */
int iot_publish_telemetry(client, json_payload);   /* topic: /{tid}/devices/{did}/telemetry */
int iot_publish_status(client, "online"|"offline"); /* topic: /{tid}/devices/{did}/status */

/* 受信 */
void iot_set_command_callback(client, cb, user_data);
int  iot_loop(client, timeout_ms);  /* process one message, blocks up to timeout_ms */

/* OTA */
int iot_ota_download(download_url, output_path, "sha256:...", ca_cert_path); /* → 0 / IOT_ERR_OTA */
```

## MCU へのポーティング

`include/iot_platform.h` の 7 関数を実装してください。  
テンプレートと ESP-IDF / FreeRTOS の実装例は `porting/` を参照。

## エラーコード

| 定数 | 値 | 意味 |
|------|----|------|
| `IOT_OK` | 0 | 成功 |
| `IOT_ERR_PROVISION` | -1 | プロビジョニング失敗 |
| `IOT_ERR_CONNECT` | -2 | MQTT 接続失敗 |
| `IOT_ERR_PUBLISH` | -3 | Publish 失敗 |
| `IOT_ERR_OTA` | -4 | ダウンロード/チェックサム失敗 |
| `IOT_ERR_BADPARAM` | -5 | NULL 引数など不正パラメータ |
