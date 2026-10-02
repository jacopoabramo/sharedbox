/* A C library that takes a box out of a "sharedbox_box" capsule; tests/test_capsule.py loads it with ctypes. */
#include <sharedbox/sharedbox_c.h>

#include <stdlib.h>

#ifdef _WIN32
#define CONSUMER_API __declspec(dllexport)
#else
#define CONSUMER_API __attribute__((visibility("default")))
#endif

/* Takes the capsule's handle; NULL if it is not a box or not one of the expected schema. */
CONSUMER_API sbx_handle *consumer_take(sbx_handle *capsule_handle, uint64_t schema_hash) {
    sbx_handle *own = (sbx_handle *)malloc(sizeof *own);
    if (own == NULL)
        return NULL;
    if (sbx_import(capsule_handle, own) != SBX_OK) {
        free(own);
        return NULL;
    }
    if (sbx_schema_hash(own) != schema_hash) {
        sbx_release(own);
        free(own);
        return NULL;
    }
    return own;
}

CONSUMER_API int consumer_read_int(const sbx_handle *h, uint16_t field, int64_t *out) {
    return sbx_read(h, field, out, sizeof *out, NULL, NULL);
}

CONSUMER_API int consumer_write_int(sbx_handle *h, uint16_t field, int64_t value) {
    sbx_value v;
    v.field = field;
    v.data = &value;
    v.len = sizeof value;
    return sbx_write(h, &v, 1, 1.0);
}

CONSUMER_API int consumer_read_datetime(const sbx_handle *h, uint16_t field, int64_t *micros, int16_t *offset) {
    sbx_datetime value;
    int rc = sbx_read_datetime(h, field, &value);
    if (rc == SBX_OK) {
        *micros = value.micros;
        *offset = value.offset_minutes;
    }
    return rc;
}

CONSUMER_API int consumer_read_position(const sbx_handle *h, uint16_t field, uint16_t *position) {
    return sbx_read_position(h, field, position);
}

CONSUMER_API void consumer_release(sbx_handle *h) {
    sbx_release(h);
    free(h);
}
