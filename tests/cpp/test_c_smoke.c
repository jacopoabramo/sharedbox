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
    sbx_release(&h);
    ok = ok && h.release == NULL && sbx_read(&h, 0, &got, sizeof got, NULL, NULL) == SBX_E_RANGE;
    smoke_remove();
    if (!ok) {
        fprintf(stderr, "c smoke: a check failed\n");
        return 1;
    }
    printf("c smoke: ok\n");
    return 0;
}
