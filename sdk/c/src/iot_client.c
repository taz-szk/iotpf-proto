/* sdk/c/src/iot_client.c */
#include "iot_client.h"
#include <MQTTClient.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Forward declarations from provisioning.c */
int iot_provision_exec(const char *api_url, const char *bootstrap_token,
                       const char *device_id, const char *cert_dir,
                       char *tenant_id_out, size_t tid_max);
int iot_load_credentials_from_dir(const char *cert_dir,
                                  char *tenant_id_out, size_t tid_max,
                                  char *device_id_out, size_t did_max);

struct iot_client_t {
    char api_url[512];
    char broker_host[256];
    int  broker_port;
    char tenant_id[64];
    char device_id[128];
    char cert_dir[512];
    MQTTClient mqtt;
    iot_command_callback_t callback;
    void *user_data;
};

static int _msg_arrived(void *ctx, char *topic, int topic_len, MQTTClient_message *msg) {
    iot_client_t *c = (iot_client_t *)ctx;
    (void)topic_len;
    if (!c->callback) {
        MQTTClient_freeMessage(&msg);
        MQTTClient_free(topic);
        return 1;
    }

    /* C-1: payload is not null-terminated — copy to heap and terminate */
    char *json = (char *)malloc((size_t)msg->payloadlen + 1);
    if (!json) {
        MQTTClient_freeMessage(&msg);
        MQTTClient_free(topic);
        return 1;
    }
    memcpy(json, msg->payload, (size_t)msg->payloadlen);
    json[msg->payloadlen] = '\0';

    /* extract "type" field with simple scan */
    char type[64] = "";
    const char *p = strstr(json, "\"type\"");
    if (p) {
        p = strchr(p + 6, '"');
        if (p) {
            p++;
            const char *end = strchr(p, '"');
            if (end) {
                size_t tlen = (size_t)(end - p);
                if (tlen < sizeof(type)) {
                    memcpy(type, p, tlen);
                    type[tlen] = '\0';
                }
            }
        }
    }

    c->callback(type, json, (size_t)msg->payloadlen, c->user_data);
    free(json);
    MQTTClient_freeMessage(&msg);
    MQTTClient_free(topic);
    return 1;
}

iot_client_t *iot_client_create(const char *api_url,
                                const char *broker_host,
                                int broker_port) {
    iot_client_t *c = (iot_client_t *)calloc(1, sizeof(iot_client_t));
    if (!c) return NULL;
    if (api_url)     strncpy(c->api_url,     api_url,     sizeof(c->api_url)     - 1);
    if (broker_host) strncpy(c->broker_host, broker_host, sizeof(c->broker_host) - 1);
    c->broker_port = broker_port > 0 ? broker_port : 8883;
    return c;
}

void iot_client_destroy(iot_client_t *client) {
    if (!client) return;
    if (client->mqtt) MQTTClient_destroy(&client->mqtt);
    free(client);
}

int iot_provision(iot_client_t *client,
                  const char *bootstrap_token,
                  const char *device_id,
                  const char *cert_dir) {
    if (!client || !bootstrap_token || !device_id || !cert_dir)
        return IOT_ERR_BADPARAM;

    int rc = iot_provision_exec(client->api_url, bootstrap_token, device_id, cert_dir,
                                client->tenant_id, sizeof(client->tenant_id));
    if (rc != IOT_OK) return rc;

    strncpy(client->device_id, device_id, sizeof(client->device_id) - 1);
    strncpy(client->cert_dir,  cert_dir,  sizeof(client->cert_dir)  - 1);
    return IOT_OK;
}

int iot_load_credentials(iot_client_t *client, const char *cert_dir) {
    if (!client || !cert_dir) return IOT_ERR_BADPARAM;

    int rc = iot_load_credentials_from_dir(cert_dir,
                                           client->tenant_id, sizeof(client->tenant_id),
                                           client->device_id, sizeof(client->device_id));
    if (rc != IOT_OK) return rc;
    strncpy(client->cert_dir, cert_dir, sizeof(client->cert_dir) - 1);
    return IOT_OK;
}

int iot_connect(iot_client_t *client) {
    if (!client || !client->tenant_id[0] || !client->device_id[0])
        return IOT_ERR_BADPARAM;

    char client_id[200];
    snprintf(client_id, sizeof(client_id), "%s:%s",
             client->tenant_id, client->device_id);

    char server_uri[320];
    snprintf(server_uri, sizeof(server_uri), "ssl://%s:%d",
             client->broker_host, client->broker_port);

    if (MQTTClient_create(&client->mqtt, server_uri, client_id,
                          MQTTCLIENT_PERSISTENCE_NONE, NULL) != MQTTCLIENT_SUCCESS)
        return IOT_ERR_CONNECT;

    /* I-1: messageArrived not registered — iot_loop() uses MQTTClient_receive() exclusively */

    char cert_path[640], key_path[640], ca_path[640];
    snprintf(cert_path, sizeof(cert_path), "%s/cert.pem", client->cert_dir);
    snprintf(key_path,  sizeof(key_path),  "%s/key.pem",  client->cert_dir);
    snprintf(ca_path,   sizeof(ca_path),   "%s/ca.pem",   client->cert_dir);

    MQTTClient_SSLOptions ssl_opts = MQTTClient_SSLOptions_initializer;
    ssl_opts.trustStore          = ca_path;
    ssl_opts.keyStore            = cert_path;
    ssl_opts.privateKey          = key_path;
    ssl_opts.enabledCipherSuites = "DEFAULT";
    ssl_opts.verify              = 1;

    MQTTClient_connectOptions conn_opts = MQTTClient_connectOptions_initializer;
    conn_opts.keepAliveInterval = 60;
    conn_opts.cleansession      = 1;
    conn_opts.ssl               = &ssl_opts;
    conn_opts.username          = client_id;
    conn_opts.password          = "";

    if (MQTTClient_connect(client->mqtt, &conn_opts) != MQTTCLIENT_SUCCESS) {
        MQTTClient_destroy(&client->mqtt);
        return IOT_ERR_CONNECT;
    }

    /* Subscribe to commands topic */
    char topic[320];
    snprintf(topic, sizeof(topic), "/%s/devices/%s/commands",
             client->tenant_id, client->device_id);
    /* I-3: check subscribe return value */
    if (MQTTClient_subscribe(client->mqtt, topic, 1) != MQTTCLIENT_SUCCESS) {
        MQTTClient_disconnect(client->mqtt, 1000);
        MQTTClient_destroy(&client->mqtt);
        return IOT_ERR_CONNECT;
    }

    /* Publish online status */
    iot_publish_status(client, "online");
    return IOT_OK;
}

void iot_disconnect(iot_client_t *client) {
    if (!client || !client->mqtt) return;
    iot_publish_status(client, "offline");
    MQTTClient_disconnect(client->mqtt, 1000);
    MQTTClient_destroy(&client->mqtt); /* I-4: release handle */
    client->mqtt = NULL;               /* I-4: prevent dangling pointer */
}

int iot_publish_telemetry(iot_client_t *client, const char *json_payload) {
    if (!client || !json_payload) return IOT_ERR_BADPARAM;

    char topic[320];
    snprintf(topic, sizeof(topic), "/%s/devices/%s/telemetry",
             client->tenant_id, client->device_id);

    MQTTClient_message msg = MQTTClient_message_initializer;
    msg.payload    = (void *)json_payload;
    msg.payloadlen = (int)strlen(json_payload);
    msg.qos        = 1;
    msg.retained   = 0;

    MQTTClient_deliveryToken token;
    int rc = MQTTClient_publishMessage(client->mqtt, topic, &msg, &token);
    if (rc != MQTTCLIENT_SUCCESS) return IOT_ERR_PUBLISH;

    MQTTClient_waitForCompletion(client->mqtt, token, 5000);
    return IOT_OK;
}

int iot_publish_status(iot_client_t *client, const char *status) {
    if (!client || !status) return IOT_ERR_BADPARAM;

    char topic[320];
    snprintf(topic, sizeof(topic), "/%s/devices/%s/status",
             client->tenant_id, client->device_id);

    char payload[64];
    snprintf(payload, sizeof(payload), "{\"status\":\"%s\"}", status);

    MQTTClient_message msg = MQTTClient_message_initializer;
    msg.payload    = payload;
    msg.payloadlen = (int)strlen(payload);
    msg.qos        = 1;
    msg.retained   = 0;

    MQTTClient_deliveryToken token;
    int rc = MQTTClient_publishMessage(client->mqtt, topic, &msg, &token);
    if (rc != MQTTCLIENT_SUCCESS) return IOT_ERR_PUBLISH;

    MQTTClient_waitForCompletion(client->mqtt, token, 5000);
    return IOT_OK;
}

void iot_set_command_callback(iot_client_t *client,
                              iot_command_callback_t cb,
                              void *user_data) {
    if (!client) return;
    client->callback  = cb;
    client->user_data = user_data;
}

int iot_loop(iot_client_t *client, int timeout_ms) {
    if (!client || !client->mqtt) return IOT_ERR_BADPARAM;
    char *topic = NULL;
    int   tlen  = 0;
    MQTTClient_message *msg = NULL;

    int rc = MQTTClient_receive(client->mqtt, &topic, &tlen, &msg, timeout_ms);
    if (rc == MQTTCLIENT_SUCCESS && msg) {
        _msg_arrived(client, topic, tlen, msg);
        /* _msg_arrived already calls MQTTClient_freeMessage / MQTTClient_free */
    }
    return (rc == MQTTCLIENT_SUCCESS) ? IOT_OK : IOT_ERR_CONNECT;
}
