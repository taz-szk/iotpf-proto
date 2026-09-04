/* sdk/c/tests/test_compile.c — link check: never executes the calls */
#include <stdio.h>
#include "iot_client.h"

int main(void) {
    printf("iot_client.h link test OK\n");

    if (0) {
        iot_client_t *c = iot_client_create(NULL, NULL, 0);
        iot_client_destroy(c);
        iot_client_set_ca_cert_path(c, NULL);
        iot_provision(c, NULL, NULL, NULL);
        iot_load_credentials(c, NULL);
        iot_connect(c);
        iot_disconnect(c);
        iot_publish_telemetry(c, NULL);
        iot_publish_status(c, NULL);
        iot_set_command_callback(c, NULL, NULL);
        iot_loop(c, 0);
        iot_ota_download(NULL, NULL, NULL, NULL);
    }
    return 0;
}
