/* sdk/c/src/provisioning.c */
#include "iot_client.h"
#include <curl/curl.h>
#include <cjson/cJSON.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_RESP 65536

struct write_buf {
    char *data;
    size_t len;
    size_t cap;
};

static size_t _write_cb(void *ptr, size_t sz, size_t nmemb, void *ud) {
    struct write_buf *b = (struct write_buf *)ud;
    size_t n = sz * nmemb;
    if (b->len + n + 1 > b->cap) return 0; /* overflow */
    memcpy(b->data + b->len, ptr, n);
    b->len += n;
    b->data[b->len] = '\0';
    return n;
}

static int _write_file(const char *path, const char *data) {
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    fputs(data, f);
    fclose(f);
    return 0;
}

static char *_read_file(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    rewind(f);
    char *buf = (char *)malloc(sz + 1);
    if (!buf) { fclose(f); return NULL; }
    fread(buf, 1, sz, f);
    buf[sz] = '\0';
    fclose(f);
    return buf;
}

int iot_provision_exec(const char *api_url,
                       const char *bootstrap_token,
                       const char *device_id,
                       const char *cert_dir,
                       char *tenant_id_out, size_t tid_max) {
    char body[512];
    snprintf(body, sizeof(body),
             "{\"bootstrap_token\":\"%s\",\"device_id\":\"%s\"}",
             bootstrap_token, device_id);

    char url[512];
    snprintf(url, sizeof(url), "%s/provision", api_url);

    char *resp_data = (char *)calloc(1, MAX_RESP);
    if (!resp_data) return IOT_ERR_PROVISION;

    struct write_buf wb = { .data = resp_data, .len = 0, .cap = MAX_RESP };

    CURL *curl = curl_easy_init();
    if (!curl) { free(resp_data); return IOT_ERR_PROVISION; }

    struct curl_slist *hdrs = curl_slist_append(NULL, "Content-Type: application/json");
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, hdrs);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, _write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &wb);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
    /* SSL peer verification disabled: device has no CA bundle before provisioning.
     * Known bootstrap problem with private CA; token is one-time-use and short-lived. */
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);

    CURLcode rc = curl_easy_perform(curl);
    long http_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    curl_slist_free_all(hdrs);
    curl_easy_cleanup(curl);

    if (rc != CURLE_OK || http_code != 200) {
        free(resp_data);
        return IOT_ERR_PROVISION;
    }

    cJSON *root = cJSON_Parse(resp_data);
    free(resp_data);
    if (!root) return IOT_ERR_PROVISION;

    int ret = IOT_ERR_PROVISION;

    const char *tid  = cJSON_GetStringValue(cJSON_GetObjectItem(root, "tenant_id"));
    const char *cert = cJSON_GetStringValue(cJSON_GetObjectItem(root, "certificate"));
    const char *key  = cJSON_GetStringValue(cJSON_GetObjectItem(root, "private_key"));
    const char *ca   = cJSON_GetStringValue(cJSON_GetObjectItem(root, "ca_certificate"));

    if (!tid || !cert || !key || !ca) goto done;

    char path[1024];
    snprintf(path, sizeof(path), "%s/cert.pem", cert_dir);
    if (_write_file(path, cert) != 0) goto done;
    snprintf(path, sizeof(path), "%s/key.pem", cert_dir);
    if (_write_file(path, key) != 0) goto done;
    snprintf(path, sizeof(path), "%s/ca.pem", cert_dir);
    if (_write_file(path, ca) != 0) goto done;
    snprintf(path, sizeof(path), "%s/tenant_id", cert_dir);
    if (_write_file(path, tid) != 0) goto done;
    snprintf(path, sizeof(path), "%s/device_id", cert_dir);
    if (_write_file(path, device_id) != 0) goto done;

    if (tenant_id_out && tid_max > 0) {
        strncpy(tenant_id_out, tid, tid_max - 1);
        tenant_id_out[tid_max - 1] = '\0';
    }
    ret = IOT_OK;

done:
    cJSON_Delete(root);
    return ret;
}

/* Reads tenant_id, device_id from cert_dir */
int iot_load_credentials_from_dir(const char *cert_dir,
                                  char *tenant_id_out, size_t tid_max,
                                  char *device_id_out, size_t did_max) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/tenant_id", cert_dir);
    char *tid = _read_file(path);
    if (!tid) return IOT_ERR_PROVISION;

    snprintf(path, sizeof(path), "%s/device_id", cert_dir);
    char *did = _read_file(path);
    if (!did) { free(tid); return IOT_ERR_PROVISION; }

    /* trim trailing newline if any */
    char *p;
    if ((p = strchr(tid, '\n'))) *p = '\0';
    if ((p = strchr(did, '\n'))) *p = '\0';

    if (tenant_id_out && tid_max > 0) {
        strncpy(tenant_id_out, tid, tid_max - 1);
        tenant_id_out[tid_max - 1] = '\0';
    }
    if (device_id_out && did_max > 0) {
        strncpy(device_id_out, did, did_max - 1);
        device_id_out[did_max - 1] = '\0';
    }
    free(tid);
    free(did);
    return IOT_OK;
}
