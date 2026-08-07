/* sdk/c/porting/iot_platform_stub.c
 *
 * Stub implementation template for iot_platform.h
 * Copy this file to your MCU project and replace each function body
 * with your platform's HTTP/MQTT/NVS implementation.
 */
#include "iot_platform.h"
#include <string.h>
#include <stdio.h>

/* ------------------------------------------------------------------ */
/* HTTP                                                                 */
/* ------------------------------------------------------------------ */

int plat_http_post(const char *url, const char *json_body,
                   char *resp_buf, size_t resp_max) {
    /* TODO: Use your MCU HTTP stack (e.g. esp_http_client, LwIP httpc).
     * Set Content-Type: application/json header.
     * Copy the response body into resp_buf (null-terminated).
     * Return 0 for HTTP 2xx, -1 otherwise. */
    (void)url; (void)json_body; (void)resp_buf; (void)resp_max;
    return -1; /* not implemented */
}

int plat_http_get_to_file(const char *url, const char *output_path) {
    /* TODO: Stream-download url and write bytes to output_path (or NVS key).
     * For OTA on embedded: write directly to the OTA partition.
     * Return 0 on success. */
    (void)url; (void)output_path;
    return -1; /* not implemented */
}

/* ------------------------------------------------------------------ */
/* File / NVS storage                                                   */
/* ------------------------------------------------------------------ */

int plat_file_write(const char *path, const char *data, size_t len) {
    /* TODO: Write `len` bytes of `data` to persistent storage.
     * On ESP-IDF: use nvs_set_blob() or SPIFFS fwrite().
     * On FreeRTOS+FatFS: use ff_fopen() / f_write(). */
    (void)path; (void)data; (void)len;
    return -1; /* not implemented */
}

int plat_file_read(const char *path, char *buf, size_t max_len) {
    /* TODO: Read up to max_len bytes from persistent storage into buf.
     * Ensure buf is null-terminated.
     * Return bytes read, or negative on error. */
    (void)path; (void)buf; (void)max_len;
    return -1; /* not implemented */
}

/* ------------------------------------------------------------------ */
/* MQTT                                                                 */
/* ------------------------------------------------------------------ */

int plat_mqtt_connect(const char *host, int port,
                      const char *cert_pem, const char *key_pem,
                      const char *ca_pem) {
    /* TODO: Connect to the MQTT broker using mTLS.
     * ClientID and Username MUST be "{tenant_id}:{device_id}".
     * On ESP-IDF: use esp_mqtt_client_config_t with .cert_pem / .key_pem.
     * On FreeRTOS: use coreMQTT + mbedTLS transport layer. */
    (void)host; (void)port;
    (void)cert_pem; (void)key_pem; (void)ca_pem;
    return -1; /* not implemented */
}

int plat_mqtt_publish(const char *topic, const char *payload, int qos) {
    /* TODO: Publish payload to topic with given QoS.
     * On ESP-IDF: esp_mqtt_client_publish().
     * On coreMQTT: MQTT_Publish(). */
    (void)topic; (void)payload; (void)qos;
    return -1; /* not implemented */
}

int plat_mqtt_subscribe(const char *topic,
                        iot_command_callback_t cb, void *user_data) {
    /* TODO: Subscribe to topic. Invoke cb(type, json, json_len, user_data)
     * for each received message. Parse "type" field from the JSON payload
     * to pass as the first argument. */
    (void)topic; (void)cb; (void)user_data;
    return -1; /* not implemented */
}
