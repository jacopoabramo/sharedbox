// sharedbox_c.cpp: the functions of sharedbox_c.h. Each converts its arguments and calls sharedbox.hpp.
#include <sharedbox/sharedbox.hpp>
#include <sharedbox/sharedbox_c.h>

#include <memory>
#include <new>

namespace {

// The sharedbox::handle behind a struct made by sbx_open or sbx_import, or nullptr for any other.
sharedbox::handle *owned(const sbx_handle *h) {
    if (h == nullptr || h->release != sharedbox::detail::release_owned)
        return nullptr;
    return static_cast<sharedbox::handle *>(h->private_data);
}

int fill(sharedbox::result<sharedbox::handle> &&opened, sbx_handle *out) {
    if (!opened)
        return static_cast<int>(opened.error());
    return static_cast<int>(sharedbox::detail::export_into(std::move(*opened), *out));
}

} // namespace

extern "C" {

int sbx_open(const char *name, double timeout, sbx_handle *out) {
    if (name == nullptr || out == nullptr)
        return SBX_E_RANGE;
    *out = sbx_handle{};
    return fill(sharedbox::handle::open(name, sharedbox::seconds(timeout)), out);
}

int sbx_import(sbx_handle *capsule, sbx_handle *out) {
    if (out == nullptr)
        return SBX_E_RANGE;
    *out = sbx_handle{};
    return fill(sharedbox::handle::from_capsule(capsule), out);
}

int sbx_read(const sbx_handle *h, uint16_t field, void *buf, size_t cap, size_t *len, uint64_t *version) {
    const sharedbox::handle *owner = owned(h);
    if (owner == nullptr || (buf == nullptr && cap > 0))
        return SBX_E_RANGE;
    const auto read = owner->read(field, {static_cast<std::byte *>(buf), cap});
    if (!read)
        return static_cast<int>(read.error());
    if (len != nullptr)
        *len = read->len;
    if (version != nullptr)
        *version = read->version;
    return read->len > cap ? SBX_E_RANGE : SBX_OK;
}

int sbx_write(sbx_handle *h, const sbx_value *values, size_t n, double lock_timeout) {
    sharedbox::handle *owner = owned(h);
    if (owner == nullptr || (n > 0 && values == nullptr))
        return SBX_E_RANGE;
    sharedbox::value few[16];
    std::unique_ptr<sharedbox::value[]> many;
    sharedbox::value *converted = few;
    if (n > std::size(few)) {
        many.reset(new (std::nothrow) sharedbox::value[n]);
        if (many == nullptr)
            return SBX_E_OS;
        converted = many.get();
    }
    for (size_t i = 0; i < n; ++i) {
        if (values[i].len > 0 && values[i].data == nullptr)
            return SBX_E_RANGE;
        converted[i] = {values[i].field, {static_cast<const std::byte *>(values[i].data), values[i].len}};
    }
    const auto written = owner->write({converted, n}, sharedbox::seconds(lock_timeout));
    return written ? SBX_OK : static_cast<int>(written.error());
}

uint64_t sbx_schema_hash(const sbx_handle *h) { return owned(h)->schema_hash(); }

void sbx_release(sbx_handle *h) {
    if (h != nullptr && h->release != nullptr)
        h->release(h);
}

} // extern "C"
