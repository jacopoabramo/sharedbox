/* sharedbox_c.h: a minimal C interface to sharedbox.hpp, which may be removed in a future major version.
 *
 * C99. The functions are defined in sharedbox_c.cpp: a C program compiles that file with a C++20 compiler
 * next to its own sources, which the CMake target sharedbox::c does for it. The status codes have the
 * values of sharedbox::status.
 */
#ifndef SHAREDBOX_SHAREDBOX_C_H
#define SHAREDBOX_SHAREDBOX_C_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SBX_OK 0
#define SBX_E_EXISTS (-1)
#define SBX_E_NOT_FOUND (-2)
#define SBX_E_LAYOUT (-3)
#define SBX_E_SCHEMA (-4)
#define SBX_E_CORRUPT (-5)
#define SBX_E_LOCK_TIMEOUT (-6)
#define SBX_E_TIMEOUT (-7)
#define SBX_E_NO_SLOT (-8)
#define SBX_E_RANGE (-9)
#define SBX_E_OS (-10)

/* A handle on one box, as a "sharedbox_box" capsule carries it between builds. private_data belongs to
 * the build that made the handle, and only its release reads it. */
typedef struct sbx_handle {
    uint16_t layout_major, layout_minor;
    uint32_t handle_version;
    void *base;
    uint64_t size;
    const char *name;
    void (*release)(struct sbx_handle *);
    void *private_data;
} sbx_handle;

/* Bytes for one field in the record encoding, without the length prefix of str and bytes. */
typedef struct sbx_value {
    uint16_t field;
    const void *data;
    size_t len;
} sbx_value;

/* Opens the box called name, waiting up to timeout seconds for a creator that has not finished. */
int sbx_open(const char *name, double timeout, sbx_handle *out);

/* Takes over the handle of a capsule made by another build: checks its segment as sbx_open does, and
 * on success clears capsule->release, so releasing out is what releases the capsule's handle. */
int sbx_import(sbx_handle *capsule, sbx_handle *out);

/* Copies the field's stored bytes into buf. If they are longer than cap, nothing is copied, *len gets
 * their length and the call returns SBX_E_RANGE. len and version may be NULL. */
int sbx_read(const sbx_handle *h, uint16_t field, void *buf, size_t cap, size_t *len, uint64_t *version);

/* Writes every value under one lock, so readers see all of them or none. */
int sbx_write(sbx_handle *h, const sbx_value *values, size_t n, double lock_timeout);

/* The schema hash the box was created with; compare it with the one the caller expects. 0 when h was
 * not made by sbx_open or sbx_import, or has been released. */
uint64_t sbx_schema_hash(const sbx_handle *h);

/* Releases any handle, including one from another build; h itself may then be reused or freed. */
void sbx_release(sbx_handle *h);

#ifdef __cplusplus
}
#endif

#endif
