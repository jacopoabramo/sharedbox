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

/* Hidden visibility gives each program or library that compiles sharedbox_c.cpp its own sbx_* functions,
 * which never resolve to another library's copy. A Windows DLL exports only what it marks for export, so
 * the macro is empty there. */
#if !defined(_WIN32) && (defined(__GNUC__) || defined(__clang__))
#define SBX_API __attribute__((visibility("hidden")))
#else
#define SBX_API
#endif

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
#define SBX_E_KIND (-11)
#define SBX_E_FOREIGN (-12)

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

/* Bytes for one field in the record encoding, without the length prefix of str, bytes and decimal. */
typedef struct sbx_value {
    uint16_t field;
    const void *data;
    size_t len;
} sbx_value;

/* A time of day: wall-clock microseconds since midnight, minutes east of UTC (0 when naive), and
 * Python's naive and fold flags as 0 or 1. */
typedef struct sbx_time {
    int64_t micros;
    int16_t offset_minutes;
    uint8_t naive, fold;
} sbx_time;

/* A date and time: microseconds since 1970-01-01T00:00, wall time when naive and UTC when aware. */
typedef struct sbx_datetime {
    int64_t micros;
    int16_t offset_minutes;
    uint8_t naive, fold;
} sbx_datetime;

typedef struct sbx_timedelta {
    int32_t days, seconds, microseconds;
} sbx_timedelta;

/* Opens the box called name, waiting up to timeout seconds for a creator that has not finished. */
SBX_API int sbx_open(const char *name, double timeout, sbx_handle *out);

/* Takes over the handle of a capsule made by another build: checks its segment as sbx_open does, and
 * on success clears capsule->release, so releasing out is what releases the capsule's handle. */
SBX_API int sbx_import(sbx_handle *capsule, sbx_handle *out);

/* Copies the field's stored bytes into buf. If they are longer than cap, nothing is copied, *len gets
 * their length and the call returns SBX_E_RANGE. len and version may be NULL. A list, set or dict field
 * gives its whole capacity: the length, then every slot, used or not. */
SBX_API int sbx_read(const sbx_handle *h, uint16_t field, void *buf, size_t cap, size_t *len, uint64_t *version);

/* Writes every value under one lock, so readers see all of them or none. SBX_E_RANGE for a field of a
 * kind this header does not know. A list, set or dict value is its length and the slots it uses, no more. */
SBX_API int sbx_write(sbx_handle *h, const sbx_value *values, size_t n, double lock_timeout);

/* The schema hash the box was created with; compare it with the one the caller expects. 0 when h was
 * not made by sbx_open or sbx_import, or has been released. */
SBX_API uint64_t sbx_schema_hash(const sbx_handle *h);

/* The kind code of a field and its description's bytes, valid until h is released; *desc is NULL and
 * *len 0 for a kind without a description. */
SBX_API int sbx_field_desc(const sbx_handle *h, uint16_t field, uint8_t *kind, const void **desc, size_t *len);

/* Typed reads and writes. A read gives SBX_E_RANGE for a field of another kind and SBX_E_CORRUPT for a
 * stored value no Python value has; a write gives SBX_E_RANGE for a value out of range. */
SBX_API int sbx_read_complex(const sbx_handle *h, uint16_t field, double out[2]);
SBX_API int sbx_write_complex(sbx_handle *h, uint16_t field, const double value[2], double lock_timeout);
/* A date as date.toordinal(): 1 is 0001-01-01. */
SBX_API int sbx_read_date(const sbx_handle *h, uint16_t field, int32_t *ordinal);
SBX_API int sbx_write_date(sbx_handle *h, uint16_t field, int32_t ordinal, double lock_timeout);
SBX_API int sbx_read_time(const sbx_handle *h, uint16_t field, sbx_time *out);
SBX_API int sbx_write_time(sbx_handle *h, uint16_t field, const sbx_time *value, double lock_timeout);
SBX_API int sbx_read_datetime(const sbx_handle *h, uint16_t field, sbx_datetime *out);
SBX_API int sbx_write_datetime(sbx_handle *h, uint16_t field, const sbx_datetime *value, double lock_timeout);
SBX_API int sbx_read_timedelta(const sbx_handle *h, uint16_t field, sbx_timedelta *out);
SBX_API int sbx_write_timedelta(sbx_handle *h, uint16_t field, const sbx_timedelta *value, double lock_timeout);
/* A UUID's 16 bytes in network order, as uuid.UUID.bytes. */
SBX_API int sbx_read_uuid(const sbx_handle *h, uint16_t field, uint8_t out[16]);
SBX_API int sbx_write_uuid(sbx_handle *h, uint16_t field, const uint8_t value[16], double lock_timeout);
/* The position of an enum member or literal value, in declaration order. */
SBX_API int sbx_read_position(const sbx_handle *h, uint16_t field, uint16_t *position);
SBX_API int sbx_write_position(sbx_handle *h, uint16_t field, uint16_t position, double lock_timeout);
SBX_API int sbx_read_flag(const sbx_handle *h, uint16_t field, uint64_t *bits);
SBX_API int sbx_write_flag(sbx_handle *h, uint16_t field, uint64_t bits, double lock_timeout);
/* Whether an optional field holds a value: *present is 1 if so, 0 for None. */
SBX_API int sbx_read_present(const sbx_handle *h, uint16_t field, int *present);

/* Releases any handle, including one from another build; h itself may then be reused or freed. */
SBX_API void sbx_release(sbx_handle *h);

#ifdef __cplusplus
}
#endif

#endif
