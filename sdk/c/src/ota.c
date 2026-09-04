/* sdk/c/src/ota.c */
#include "iot_client.h"
#include <curl/curl.h>
#include <openssl/sha.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct file_ctx {
    FILE *fp;
    SHA256_CTX sha_ctx;
};

static size_t _write_file_cb(void *ptr, size_t sz, size_t nmemb, void *ud) {
    struct file_ctx *ctx = (struct file_ctx *)ud;
    size_t n = sz * nmemb;
    SHA256_Update(&ctx->sha_ctx, ptr, n);
    return fwrite(ptr, 1, n, ctx->fp);
}

int iot_ota_download(const char *download_url,
                     const char *output_path,
                     const char *expected_sha256,
                     const char *ca_cert_path) {
    if (!download_url || !output_path || !expected_sha256)
        return IOT_ERR_BADPARAM;

    /* Strip "sha256:" prefix if present */
    const char *expected = expected_sha256;
    if (strncmp(expected, "sha256:", 7) == 0)
        expected += 7;
    if (strlen(expected) != 64)
        return IOT_ERR_BADPARAM;

    FILE *fp = fopen(output_path, "wb");
    if (!fp) return IOT_ERR_OTA;

    struct file_ctx ctx;
    ctx.fp = fp;
    SHA256_Init(&ctx.sha_ctx);

    CURL *curl = curl_easy_init();
    if (!curl) { fclose(fp); return IOT_ERR_OTA; }

    curl_easy_setopt(curl, CURLOPT_URL, download_url);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, _write_file_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &ctx);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 300L);
    /* Certificate verification is always on (see iot_client_set_ca_cert_path()
     * doc comment for the private-CA case). The SHA256 checksum only protects
     * file integrity after the fact; without TLS verification an attacker could
     * still read the download URL's embedded JWT token in transit. */
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 2L);
    if (ca_cert_path && ca_cert_path[0]) {
        curl_easy_setopt(curl, CURLOPT_CAINFO, ca_cert_path);
    }

    CURLcode rc = curl_easy_perform(curl);
    long http_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    curl_easy_cleanup(curl);
    fclose(fp);

    if (rc != CURLE_OK || http_code != 200) {
        remove(output_path); /* I-2: remove partial file on download failure */
        return IOT_ERR_OTA;
    }

    unsigned char digest[32];
    SHA256_Final(digest, &ctx.sha_ctx);

    char actual[65];
    for (int i = 0; i < 32; i++)
        snprintf(actual + i * 2, 3, "%02x", digest[i]);
    actual[64] = '\0';

    if (strncmp(actual, expected, 64) != 0) {
        remove(output_path); /* I-2: remove file with bad checksum */
        return IOT_ERR_OTA;
    }

    return IOT_OK;
}
