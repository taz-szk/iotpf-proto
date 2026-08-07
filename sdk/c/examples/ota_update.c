/* sdk/c/examples/ota_update.c
 * Usage (provision + OTA):
 *   export BOOTSTRAP_TOKEN=<token>
 *   export DEVICE_ID=my-device-001
 *   export IOT_API_URL=https://localhost/api
 *   export MQTT_BROKER=localhost
 *   export CERT_DIR=./certs
 *   ./ota_update
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include "iot_client.h"

static volatile int running = 1;
static void _sig(int s) { (void)s; running = 0; }

static void on_command(const char *type, const char *json,
                       size_t json_len, void *user_data) {
    (void)user_data; (void)json_len;

    if (strcmp(type, "ota") != 0) {
        printf("Unknown command: %s\n", type);
        return;
    }

    printf("OTA command received\n");

    /* Extract download_url */
    const char *url_key = "\"download_url\":\"";
    const char *p = strstr(json, url_key);
    if (!p) { fprintf(stderr, "No download_url in payload\n"); return; }
    p += strlen(url_key);
    const char *end = strchr(p, '"');
    if (!end) { fprintf(stderr, "Malformed download_url\n"); return; }

    char url[512];
    size_t url_len = (size_t)(end - p);
    if (url_len >= sizeof(url)) { fprintf(stderr, "URL too long\n"); return; }
    memcpy(url, p, url_len);
    url[url_len] = '\0';

    /* Extract checksum */
    const char *cs_key = "\"checksum\":\"";
    p = strstr(json, cs_key);
    char checksum[80] = "";
    if (p) {
        p += strlen(cs_key);
        end = strchr(p, '"');
        if (end) {
            size_t clen = (size_t)(end - p);
            if (clen < sizeof(checksum)) {
                memcpy(checksum, p, clen);
                checksum[clen] = '\0';
            }
        }
    }

    const char *fw_path = "/tmp/firmware_update.bin";
    printf("Downloading from: %s\n", url);
    int rc = iot_ota_download(url, fw_path, checksum);
    if (rc == IOT_OK) {
        printf("Firmware downloaded and verified: %s\n", fw_path);
        printf("TODO: apply firmware (platform-specific)\n");
    } else {
        fprintf(stderr, "OTA download/verify failed: %d\n", rc);
    }
}

int main(void) {
    const char *api_url     = getenv("IOT_API_URL")    ?: "https://localhost/api";
    const char *broker_host = getenv("MQTT_BROKER")    ?: "localhost";
    const char *cert_dir    = getenv("CERT_DIR")       ?: "./certs";
    const char *bootstrap   = getenv("BOOTSTRAP_TOKEN");
    const char *device_id   = getenv("DEVICE_ID")      ?: "my-device-001";
    int         broker_port = atoi(getenv("MQTT_PORT") ?: "8883");
    if (broker_port == 0) broker_port = 8883;

    signal(SIGINT,  _sig);
    signal(SIGTERM, _sig);

    iot_client_t *client = iot_client_create(api_url, broker_host, broker_port);
    if (!client) { fprintf(stderr, "create failed\n"); return 1; }

    /* Provision if BOOTSTRAP_TOKEN provided and no existing creds */
    if (bootstrap) {
        printf("Provisioning device '%s'...\n", device_id);
        if (iot_provision(client, bootstrap, device_id, cert_dir) != IOT_OK) {
            fprintf(stderr, "Provisioning failed\n");
            iot_client_destroy(client);
            return 1;
        }
        printf("Provisioned — certs saved to %s\n", cert_dir);
    } else {
        if (iot_load_credentials(client, cert_dir) != IOT_OK) {
            fprintf(stderr, "load_credentials failed — set BOOTSTRAP_TOKEN to provision\n");
            iot_client_destroy(client);
            return 1;
        }
    }

    iot_set_command_callback(client, on_command, NULL);

    if (iot_connect(client) != IOT_OK) {
        fprintf(stderr, "connect failed\n");
        iot_client_destroy(client);
        return 1;
    }

    printf("Waiting for OTA commands. Press Ctrl+C to stop.\n");
    while (running) {
        iot_loop(client, 1000);
    }

    iot_disconnect(client);
    iot_client_destroy(client);
    return 0;
}
