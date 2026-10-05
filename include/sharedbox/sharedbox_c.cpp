// sharedbox_c.cpp: the functions of sharedbox_c.h. Each converts its arguments and calls sharedbox.hpp.
#include <sharedbox/sharedbox.hpp>
#include <sharedbox/sharedbox_c.h>

#include <array>
#include <chrono>
#include <complex>
#include <cstring>
#include <memory>
#include <new>
#include <span>

namespace {

using bytes_in = std::span<const std::byte>;
using bytes_out = std::span<std::byte>;

// The sharedbox::handle behind a struct made by sbx_open or sbx_import, or nullptr for any other.
sharedbox::handle *owned(const sbx_handle *h) {
    if (h == nullptr || h->release != sharedbox::detail::release_owned)
        return nullptr;
    return static_cast<sharedbox::handle *>(h->private_data);
}

// Reads field, if its kind passes kind_ok, into a buffer of its size and hands the bytes to use.
template <class KindOk, class Use> int read_as(const sbx_handle *h, uint16_t field, KindOk kind_ok, Use use) {
    const sharedbox::handle *owner = owned(h);
    if (owner == nullptr || field >= owner->field_count() || !kind_ok(owner->field(field).kind))
        return SBX_E_RANGE;
    const std::size_t size = owner->field(field).capacity;
    std::byte small[16];
    std::unique_ptr<std::byte[]> large;
    std::byte *buf = small;
    if (size > sizeof small) {
        large.reset(new (std::nothrow) std::byte[size]);
        if (large == nullptr)
            return SBX_E_OS;
        buf = large.get();
    }
    const auto read = owner->read(field, {buf, size});
    if (!read)
        return static_cast<int>(read.error());
    return use(owner->field_type(field), bytes_in(buf, read->len));
}

// Encodes into a buffer of N bytes with encode, then writes it to field if its kind is kind.
template <std::size_t N, class Encode>
int write_as(sbx_handle *h, uint16_t field, std::uint8_t kind, double lock_timeout, Encode encode) {
    sharedbox::handle *owner = owned(h);
    if (owner == nullptr || field >= owner->field_count() || owner->field(field).kind != kind)
        return SBX_E_RANGE;
    std::array<std::byte, N> buf{};
    if (const sharedbox::status rc = encode(owner->field_type(field), bytes_out(buf)); rc != sharedbox::status::ok)
        return static_cast<int>(rc);
    const sharedbox::value v{field, buf};
    const auto written = owner->write({&v, 1}, sharedbox::seconds(lock_timeout));
    return written ? SBX_OK : static_cast<int>(written.error());
}

auto is(std::uint8_t kind) {
    return [kind](std::uint8_t k) { return k == kind; };
}

bool positioned(std::uint8_t k) { return k == sharedbox::kind_enum || k == sharedbox::kind_literal; }

template <class T> int stored(sharedbox::result<T> &&got, T *out) {
    if (!got)
        return static_cast<int>(got.error());
    *out = *got;
    return SBX_OK;
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

uint64_t sbx_schema_hash(const sbx_handle *h) {
    const sharedbox::handle *owner = owned(h);
    return owner == nullptr ? 0 : owner->schema_hash();
}

int sbx_field_desc(const sbx_handle *h, uint16_t field, uint8_t *kind, const void **desc, size_t *len) {
    const sharedbox::handle *owner = owned(h);
    if (owner == nullptr || kind == nullptr || desc == nullptr || len == nullptr || field >= owner->field_count())
        return SBX_E_RANGE;
    const auto bytes = owner->field_type(field).description();
    *kind = owner->field(field).kind;
    *desc = bytes.empty() ? nullptr : bytes.data();
    *len = bytes.size();
    return SBX_OK;
}

int sbx_read_complex(const sbx_handle *h, uint16_t field, double out[2]) {
    if (out == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_complex), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_complex(b);
        if (!got)
            return static_cast<int>(got.error());
        out[0] = got->real();
        out[1] = got->imag();
        return SBX_OK;
    });
}

int sbx_write_complex(sbx_handle *h, uint16_t field, const double value[2], double lock_timeout) {
    if (value == nullptr)
        return SBX_E_RANGE;
    return write_as<16>(h, field, sharedbox::kind_complex, lock_timeout, [&](sharedbox::type_view, bytes_out b) {
        return sharedbox::encode_complex({value[0], value[1]}, b);
    });
}

int sbx_read_date(const sbx_handle *h, uint16_t field, int32_t *ordinal) {
    if (ordinal == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_date), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_date(b);
        if (!got)
            return static_cast<int>(got.error());
        *ordinal = static_cast<int32_t>(got->time_since_epoch().count() + sharedbox::unix_epoch_ordinal);
        return SBX_OK;
    });
}

int sbx_write_date(sbx_handle *h, uint16_t field, int32_t ordinal, double lock_timeout) {
    return write_as<4>(h, field, sharedbox::kind_date, lock_timeout, [&](sharedbox::type_view, bytes_out b) {
        return sharedbox::encode_date(
            std::chrono::sys_days(std::chrono::days(std::int64_t{ordinal} - sharedbox::unix_epoch_ordinal)), b);
    });
}

int sbx_read_time(const sbx_handle *h, uint16_t field, sbx_time *out) {
    if (out == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_time), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_time(b);
        if (!got)
            return static_cast<int>(got.error());
        *out = {got->of_day.count(), got->offset, static_cast<uint8_t>(got->naive),
                static_cast<uint8_t>(got->fold)};
        return SBX_OK;
    });
}

int sbx_write_time(sbx_handle *h, uint16_t field, const sbx_time *value, double lock_timeout) {
    if (value == nullptr)
        return SBX_E_RANGE;
    return write_as<16>(h, field, sharedbox::kind_time, lock_timeout, [&](sharedbox::type_view, bytes_out b) {
        return sharedbox::encode_time(
            {std::chrono::microseconds(value->micros), value->offset_minutes, value->naive != 0, value->fold != 0},
            b);
    });
}

int sbx_read_datetime(const sbx_handle *h, uint16_t field, sbx_datetime *out) {
    if (out == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_datetime), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_datetime(b);
        if (!got)
            return static_cast<int>(got.error());
        *out = {got->micros, got->offset, static_cast<uint8_t>(got->naive), static_cast<uint8_t>(got->fold)};
        return SBX_OK;
    });
}

int sbx_write_datetime(sbx_handle *h, uint16_t field, const sbx_datetime *value, double lock_timeout) {
    if (value == nullptr)
        return SBX_E_RANGE;
    return write_as<16>(h, field, sharedbox::kind_datetime, lock_timeout, [&](sharedbox::type_view, bytes_out b) {
        return sharedbox::encode_datetime(
            {value->micros, value->offset_minutes, value->naive != 0, value->fold != 0}, b);
    });
}

int sbx_read_timedelta(const sbx_handle *h, uint16_t field, sbx_timedelta *out) {
    if (out == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_timedelta), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_timedelta(b);
        if (!got)
            return static_cast<int>(got.error());
        *out = {got->days, got->seconds, got->microseconds};
        return SBX_OK;
    });
}

int sbx_write_timedelta(sbx_handle *h, uint16_t field, const sbx_timedelta *value, double lock_timeout) {
    if (value == nullptr)
        return SBX_E_RANGE;
    return write_as<12>(h, field, sharedbox::kind_timedelta, lock_timeout, [&](sharedbox::type_view, bytes_out b) {
        return sharedbox::encode_timedelta({value->days, value->seconds, value->microseconds}, b);
    });
}

int sbx_read_uuid(const sbx_handle *h, uint16_t field, uint8_t out[16]) {
    if (out == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_uuid), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_uuid(b);
        if (!got)
            return static_cast<int>(got.error());
        std::memcpy(out, got->data(), 16);
        return SBX_OK;
    });
}

int sbx_write_uuid(sbx_handle *h, uint16_t field, const uint8_t value[16], double lock_timeout) {
    if (value == nullptr)
        return SBX_E_RANGE;
    return write_as<16>(h, field, sharedbox::kind_uuid, lock_timeout, [&](sharedbox::type_view, bytes_out b) {
        std::array<std::byte, 16> raw;
        std::memcpy(raw.data(), value, 16);
        return sharedbox::encode_uuid(raw, b);
    });
}

int sbx_read_position(const sbx_handle *h, uint16_t field, uint16_t *position) {
    if (position == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, positioned, [&](sharedbox::type_view t, bytes_in b) {
        return stored(sharedbox::decode_position(t, b), position);
    });
}

int sbx_write_position(sbx_handle *h, uint16_t field, uint16_t position, double lock_timeout) {
    const sharedbox::handle *owner = owned(h);
    if (owner == nullptr || field >= owner->field_count() || !positioned(owner->field(field).kind))
        return SBX_E_RANGE;
    return write_as<2>(h, field, owner->field(field).kind, lock_timeout, [&](sharedbox::type_view t, bytes_out b) {
        return sharedbox::encode_position(t, position, b);
    });
}

int sbx_read_flag(const sbx_handle *h, uint16_t field, uint64_t *bits) {
    if (bits == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_flag),
                   [&](sharedbox::type_view, bytes_in b) { return stored(sharedbox::decode_flag(b), bits); });
}

int sbx_write_flag(sbx_handle *h, uint16_t field, uint64_t bits, double lock_timeout) {
    return write_as<8>(h, field, sharedbox::kind_flag, lock_timeout,
                       [&](sharedbox::type_view, bytes_out b) { return sharedbox::encode_flag(bits, b); });
}

int sbx_read_present(const sbx_handle *h, uint16_t field, int *present) {
    if (present == nullptr)
        return SBX_E_RANGE;
    return read_as(h, field, is(sharedbox::kind_optional), [&](sharedbox::type_view, bytes_in b) {
        const auto got = sharedbox::decode_present(b);
        if (!got)
            return static_cast<int>(got.error());
        *present = *got ? 1 : 0;
        return SBX_OK;
    });
}

void sbx_release(sbx_handle *h) {
    if (h != nullptr && h->release != nullptr)
        h->release(h);
}

} // extern "C"
