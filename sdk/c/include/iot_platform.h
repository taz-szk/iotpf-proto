/* sdk/c/include/iot_platform.h
 *
 * MCU Porting Interface for IoT Platform SDK
 *
 * Implement all functions below for your target MCU/RTOS.
 * On Linux, these are implemented by libcurl + paho in iot_client.c.
 * On MCU, implement them using your network/MQTT stack and NVS.
 */
#ifndef IOT_PLATFORM_H
#define IOT_PLATFORM_H

#include <stddef.h>
#include "iot_client.h"   /* for iot_command_callback_t */

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- HTTP ---------- */

/**
 * plat_http_post - Send HTTP POST with JSON body, receive response.
 * @url:       Null-terminated URL (may be HTTP or HTTPS).
 * @json_body: Null-terminated JSON request body.
 * @resp_buf:  Buffer for the response body (null-terminated on success).
 * @resp_max:  Size of resp_buf in bytes.
 *
 * Returns 0 on success (HTTP 2xx), negative on error.
 */
int plat_http_post(const char *url, const char *json_body,
                   char *resp_buf, size_t resp_max);

/**
 * plat_http_get_to_file - Download URL to a local file (or NVS key).
 * @url:         Null-terminated download URL.
 * @output_path: File path or NVS key to write firmware bytes to.
 *
 * Returns 0 on success, negative on error.
 */
int plat_http_get_to_file(const char *url, const char *output_path);

/* ---------- File / NVS storage ---------- */

/**
 * plat_file_write - Persist data to storage.
 * @path:  File path or NVS key.
 * @data:  Byte array to write.
 * @len:   Number of bytes.
 *
 * Returns 0 on success, negative on error.
 */
int plat_file_write(const char *path, const char *data, size_t len);

/**
 * plat_file_read - Read data from storage.
 * @path:    File path or NVS key.
 * @buf:     Output buffer.
 * @max_len: Maximum bytes to read (including null terminator).
 *
 * Returns number of bytes read, or negative on error.
 */
int plat_file_read(const char *path, char *buf, size_t max_len);

/* ---------- MQTT ---------- */

/**
 * plat_mqtt_connect - Establish mTLS MQTT connection.
 * @host:     Broker hostname or IP.
 * @port:     Broker port (typically 8883).
 * @cert_pem: Client certificate in PEM format (null-terminated).
 * @key_pem:  Client private key in PEM format (null-terminated).
 * @ca_pem:   CA certificate in PEM format (null-terminated).
 *
 * ClientID and username MUST be set to "{tenant_id}:{device_id}".
 * Returns 0 on success, negative on error.
 */
int plat_mqtt_connect(const char *host, int port,
                      const char *cert_pem, const char *key_pem,
                      const char *ca_pem);

/**
 * plat_mqtt_publish - Publish a message to a topic.
 * @topic:   Null-terminated MQTT topic.
 * @payload: Null-terminated payload string.
 * @qos:     Quality of service (0, 1, or 2).
 *
 * Returns 0 on success, negative on error.
 */
int plat_mqtt_publish(const char *topic, const char *payload, int qos);

/**
 * plat_mqtt_subscribe - Subscribe to a topic with a message callback.
 * @topic:     Null-terminated MQTT topic to subscribe to.
 * @cb:        Callback invoked on each received message.
 * @user_data: Opaque pointer passed through to cb.
 *
 * Returns 0 on success, negative on error.
 */
int plat_mqtt_subscribe(const char *topic,
                        iot_command_callback_t cb, void *user_data);

#ifdef __cplusplus
}
#endif

#endif /* IOT_PLATFORM_H */
