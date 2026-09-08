/* sdk/c/include/iot_client.h */
#ifndef IOT_CLIENT_H
#define IOT_CLIENT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Error codes */
#define IOT_OK              0
#define IOT_ERR_PROVISION  -1
#define IOT_ERR_CONNECT    -2
#define IOT_ERR_PUBLISH    -3
#define IOT_ERR_OTA        -4
#define IOT_ERR_BADPARAM   -5

typedef struct iot_client_t iot_client_t;

typedef void (*iot_command_callback_t)(
    const char *type,       /* null-terminated command type, e.g. "ota" */
    const char *json,       /* raw JSON payload */
    size_t json_len,
    void *user_data
);

/* Lifecycle */
iot_client_t *iot_client_create(const char *api_url,
                                const char *broker_host,
                                int broker_port);
void iot_client_destroy(iot_client_t *client);

/* Provisioning */
int iot_provision(iot_client_t *client,
                  const char *bootstrap_token,
                  const char *device_id,
                  const char *cert_dir);
int iot_load_credentials(iot_client_t *client, const char *cert_dir);

/* TLS trust for the provisioning HTTPS call (before device certs exist).
 * Optional: call before iot_provision(). If never called, or called with
 * NULL/"", the platform's default CA bundle (system trust store) is used,
 * which is correct when the server presents a publicly-trusted certificate
 * (e.g. Let's Encrypt). If the deployment's API endpoint presents a
 * certificate from a private CA (e.g. step-ca in local/dev setups), pass
 * the path to that CA's root certificate (PEM) here — it must be
 * provisioned onto the device out-of-band (e.g. baked into firmware).
 * Certificate verification is never disabled. */
int iot_client_set_ca_cert_path(iot_client_t *client, const char *ca_cert_path);

/* Connection */
int  iot_connect(iot_client_t *client);
void iot_disconnect(iot_client_t *client);

/* Publish */
int iot_publish_telemetry(iot_client_t *client, const char *json_payload);
int iot_publish_status(iot_client_t *client, const char *status);

/* Subscribe / receive */
void iot_set_command_callback(iot_client_t *client,
                              iot_command_callback_t cb,
                              void *user_data);
int iot_loop(iot_client_t *client, int timeout_ms);

/* OTA download + SHA256 verify.
 * ca_cert_path: same meaning as iot_client_set_ca_cert_path(); pass NULL
 * to use the system default CA bundle. Certificate verification is never
 * disabled. */
int iot_ota_download(const char *download_url,
                     const char *output_path,
                     const char *expected_sha256,
                     const char *ca_cert_path);

#ifdef __cplusplus
}
#endif

#endif /* IOT_CLIENT_H */
