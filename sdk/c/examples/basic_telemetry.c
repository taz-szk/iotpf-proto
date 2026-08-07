/* sdk/c/examples/basic_telemetry.c
 * Usage:
 *   export CERT_DIR=./certs
 *   export MQTT_BROKER=localhost
 *   ./basic_telemetry
 *
 * Run ota_update first to provision, or set CERT_DIR to an existing dir.
 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include "iot_client.h"

static volatile int running = 1;
static void _sig(int s) { (void)s; running = 0; }

int main(void) {
    const char *cert_dir    = getenv("CERT_DIR")      ?: "./certs";
    const char *broker_host = getenv("MQTT_BROKER")   ?: "localhost";
    int         broker_port = atoi(getenv("MQTT_PORT") ?: "8883");
    if (broker_port == 0) broker_port = 8883;

    signal(SIGINT,  _sig);
    signal(SIGTERM, _sig);

    iot_client_t *client = iot_client_create("", broker_host, broker_port);
    if (!client) { fprintf(stderr, "create failed\n"); return 1; }

    if (iot_load_credentials(client, cert_dir) != IOT_OK) {
        fprintf(stderr, "load_credentials failed — run provisioning first\n");
        iot_client_destroy(client);
        return 1;
    }

    if (iot_connect(client) != IOT_OK) {
        fprintf(stderr, "connect failed\n");
        iot_client_destroy(client);
        return 1;
    }

    printf("Connected. Publishing telemetry every 5s. Press Ctrl+C to stop.\n");

    int tick = 0;
    while (running) {
        char payload[128];
        snprintf(payload, sizeof(payload),
                 "{\"temperature\":%.1f,\"humidity\":%.1f,\"tick\":%d}",
                 20.0 + (tick % 10) * 0.5,
                 55.0 + (tick % 5),
                 tick);

        if (iot_publish_telemetry(client, payload) == IOT_OK)
            printf("Published: %s\n", payload);
        else
            fprintf(stderr, "publish failed\n");

        iot_loop(client, 5000);
        tick++;
    }

    iot_disconnect(client);
    iot_client_destroy(client);
    return 0;
}
