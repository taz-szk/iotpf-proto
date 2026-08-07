# IoT Platform C SDK — MCU ポーティングガイド

## 概要

`iot_client.h` は Linux 向けフル実装（paho-mqtt / libcurl / OpenSSL）です。
MCU でこのプラットフォームへ接続する場合は、`iot_platform.h` に定義された
6 つの関数を実装してください。Linux 側のコードは一切不要です。

## 実装する関数

| 関数 | 責務 |
|------|------|
| `plat_http_post` | HTTPS POST（プロビジョニング） |
| `plat_http_get_to_file` | HTTPS GET ストリーミングダウンロード（OTA） |
| `plat_file_write` | 証明書・ID を不揮発ストレージに保存 |
| `plat_file_read` | 不揮発ストレージから読み込み |
| `plat_mqtt_connect` | mTLS MQTT 接続（ClientID = `{tenant_id}:{device_id}`） |
| `plat_mqtt_publish` | MQTT publish |
| `plat_mqtt_subscribe` | MQTT subscribe + コールバック設定 |

## 接続仕様

- **ポート**: 8883（mTLS 必須）
- **ClientID**: `{tenant_id}:{device_id}` — プロビジョニング後に `tenant_id` ファイルと `device_id` ファイルに保存
- **Username**: `{tenant_id}:{device_id}`（パスワード不要）
- **TLS**: クライアント証明書（`cert.pem`）+ 秘密鍵（`key.pem`）+ CA 証明書（`ca.pem`）

## テレメトリペイロード

```json
{"temperature": 25.3, "humidity": 60.1}
```
数値フィールドのみが InfluxDB に記録されます。

## OTA フロー

1. `/{tenant_id}/devices/{device_id}/commands` をサブスクライブ
2. 受信 JSON の `"type"` フィールドが `"ota"` か確認
3. `"download_url"` へ HTTP GET（認証不要、URL にトークン含む）
4. `"checksum"` で SHA256 検証（`"sha256:..."` プレフィックスを除去して比較）
5. プラットフォーム固有のファームウェア適用（OTA パーティションへの書き込みなど）

## ESP-IDF (ESP32) 実装例

```c
/* plat_http_post — ESP-IDF */
#include "esp_http_client.h"
#include <string.h>

static char _resp[8192];
static int  _resp_len;

static esp_err_t _http_event(esp_http_client_event_t *evt) {
    if (evt->event_id == HTTP_EVENT_ON_DATA) {
        int n = evt->data_len;
        if (_resp_len + n < (int)sizeof(_resp)) {
            memcpy(_resp + _resp_len, evt->data, n);
            _resp_len += n;
        }
    }
    return ESP_OK;
}

int plat_http_post(const char *url, const char *json_body,
                   char *resp_buf, size_t resp_max) {
    _resp_len = 0;
    esp_http_client_config_t cfg = {
        .url            = url,
        .event_handler  = _http_event,
        .skip_cert_common_name_check = true,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, json_body, strlen(json_body));

    esp_err_t err = esp_http_client_perform(client);
    int code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || code < 200 || code >= 300) return -1;

    _resp[_resp_len] = '\0';
    size_t copy = (_resp_len + 1 < resp_max) ? (size_t)_resp_len + 1 : resp_max;
    memcpy(resp_buf, _resp, copy);
    resp_buf[resp_max - 1] = '\0';
    return 0;
}
```

## FreeRTOS + coreMQTT 実装例（接続のみ）

```c
/* plat_mqtt_connect — coreMQTT + mbedTLS */
int plat_mqtt_connect(const char *host, int port,
                      const char *cert_pem, const char *key_pem,
                      const char *ca_pem) {
    /* 1. TLS transport 初期化 (mbedTLS) */
    /* 2. MQTT_Connect() で接続 */
    /* 3. MQTT_Subscribe() で commands トピックを購読 */
    /* 詳細は coreMQTT ドキュメント参照:
       https://freertos.org/Documentation/02-Kernel/04-API-references/
       10-Supplemental-APIs/02-MQTT */
    return 0; /* TODO */
}
```

## NVS ストレージキー命名規則（ESP-IDF）

| `plat_file_write` の `path` 引数 | NVS キー |
|----------------------------------|----------|
| `cert_dir/cert.pem` | `cert_pem` |
| `cert_dir/key.pem` | `key_pem` |
| `cert_dir/ca.pem` | `ca_pem` |
| `cert_dir/tenant_id` | `tenant_id` |
| `cert_dir/device_id` | `device_id` |

`path` を `/` でスプリットし、最後のコンポーネントを NVS キーとして使用してください。
