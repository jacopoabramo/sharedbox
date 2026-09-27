/* sharedbox_c.h: the handle a capsule carries and the status codes, for C and C++. */
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

#ifdef __cplusplus
}
#endif

#endif
