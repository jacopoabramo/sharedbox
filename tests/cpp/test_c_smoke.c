/* sharedbox_c.h from C99: open a box made through the C++ API, write, read the value back, release. */
#include <sharedbox/sharedbox_c.h>

#include <stdio.h>

/* Defined in test_c_smoke_box.cpp, since creating and unlinking are in the C++ API only. The name
 * returned stays valid until smoke_remove. */
const char *smoke_create(void);
void smoke_remove(void);

int main(void) {
    const char *name = smoke_create();
    sbx_handle h;
    int64_t written = -42, got = 0;
    uint64_t version = 0;
    size_t len = 0;
    sbx_value value;
    int ok;
    if (name == NULL) {
        fprintf(stderr, "could not create the box\n");
        return 1;
    }
    if (sbx_open(name, 1.0, &h) != SBX_OK) {
        fprintf(stderr, "sbx_open failed\n");
        smoke_remove();
        return 1;
    }
    value.field = 0;
    value.data = &written;
    value.len = sizeof written;
    ok = sbx_schema_hash(&h) == 0x5EED;
    ok = ok && sbx_write(&h, &value, 1, 1.0) == SBX_OK;
    ok = ok && sbx_read(&h, 0, &got, sizeof got, &len, &version) == SBX_OK;
    ok = ok && got == written && len == sizeof got && version == 1;
    ok = ok && sbx_read(&h, 0, &got, 4, &len, NULL) == SBX_E_RANGE && len == 8;
    ok = ok && sbx_read(&h, 1, &got, sizeof got, NULL, NULL) == SBX_E_RANGE;
    {
        sbx_datetime when, back = {0, 0, 0, 0};
        double z[2] = {1.5, -2.0}, zback[2] = {0, 0};
        uint16_t position = 0;
        uint8_t kind = 0;
        const void *desc = NULL;
        size_t desc_len = 0;
        when.micros = 1700000000000000;
        when.offset_minutes = 120;
        when.naive = 0;
        when.fold = 0;
        ok = ok && sbx_write_datetime(&h, 1, &when, 1.0) == SBX_OK;
        ok = ok && sbx_read_datetime(&h, 1, &back) == SBX_OK;
        ok = ok && back.micros == when.micros && back.offset_minutes == 120 && back.naive == 0;
        when.offset_minutes = 1440;
        ok = ok && sbx_write_datetime(&h, 1, &when, 1.0) == SBX_E_RANGE;
        ok = ok && sbx_write_complex(&h, 2, z, 1.0) == SBX_OK;
        ok = ok && sbx_read_complex(&h, 2, zback) == SBX_OK && zback[0] == 1.5 && zback[1] == -2.0;
        ok = ok && sbx_write_position(&h, 3, 1, 1.0) == SBX_OK;
        ok = ok && sbx_read_position(&h, 3, &position) == SBX_OK && position == 1;
        ok = ok && sbx_write_position(&h, 3, 2, 1.0) == SBX_E_RANGE;
        ok = ok && sbx_read_complex(&h, 1, zback) == SBX_E_RANGE;
        /* Kind 64 is enum. Its description is an 8-byte head, the names OFF and ON with their u16
         * lengths (9 bytes), padded to 24. */
        ok = ok && sbx_field_desc(&h, 3, &kind, &desc, &desc_len) == SBX_OK;
        ok = ok && kind == 64 && desc != NULL && desc_len == 24;
        /* Kind 1 is int, which has no description. */
        ok = ok && sbx_field_desc(&h, 0, &kind, &desc, &desc_len) == SBX_OK;
        ok = ok && kind == 1 && desc == NULL && desc_len == 0;
    }
    sbx_release(&h);
    ok = ok && h.release == NULL && sbx_read(&h, 0, &got, sizeof got, NULL, NULL) == SBX_E_RANGE;
    ok = ok && sbx_schema_hash(&h) == 0 && sbx_schema_hash(NULL) == 0;
    smoke_remove();
    if (!ok) {
        fprintf(stderr, "c smoke: a check failed\n");
        return 1;
    }
    printf("c smoke: ok\n");
    return 0;
}
