// sharedbox.hpp: layout 2.0 of a sharedbox segment, the 1.0 layout it still opens, and the protocols that
// use them.
//
// C++20, header-only, 64-bit little-endian targets, no exceptions. Names in sharedbox::detail are
// internal and may change in any release.
#ifndef SHAREDBOX_SHAREDBOX_HPP
#define SHAREDBOX_SHAREDBOX_HPP

#include <sharedbox/sharedbox_c.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <memory>
#include <new>
#include <span>
#include <string_view>
#include <thread>
#include <type_traits>
#include <utility>
#include <variant>
#include <version>

#if defined(__cpp_lib_expected) && __cpp_lib_expected >= 202211L
#include <expected>
#endif

#ifdef _WIN32
// Defined only for windows.h, and removed again at the end of this header.
#ifndef NOMINMAX
#define NOMINMAX
#define SHAREDBOX_DEFINED_NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#define SHAREDBOX_DEFINED_WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <bcrypt.h>
#ifdef _MSC_VER
#pragma comment(lib, "bcrypt.lib")
#endif
#else
#include <cerrno>
#include <climits>
#include <ctime>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>
#endif

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "sharedbox.hpp supports little-endian targets only"
#endif

#if defined(_MSC_VER)
#define SHAREDBOX_HOT __forceinline
#else
#define SHAREDBOX_HOT inline __attribute__((always_inline))
#endif

namespace sharedbox {
// Renamed when this header's C++ interface changes incompatibly, so code built against headers with
// different inline namespaces can be linked into one program without their names colliding. Names in
// detail may change without a rename, so a shared library should build with hidden visibility.
inline namespace v3 {

inline constexpr std::uint16_t layout_major = 2;
inline constexpr std::uint16_t layout_minor = 0;
// The oldest major version open and from_capsule still accept.
inline constexpr std::uint16_t oldest_layout_major = 1;
inline constexpr std::uint32_t handle_version = 1;
// "SHREDBX1" read as a little-endian u64.
inline constexpr std::uint64_t magic = 0x3158424445524853;
inline constexpr std::uint32_t header_size = 128;
inline constexpr std::size_t name_max = 128;
inline constexpr std::uint32_t max_fields = 256;
inline constexpr std::uint32_t max_capacity = std::uint32_t{1} << 20;
inline constexpr std::uint32_t max_waiter_slots = 4096;
inline constexpr std::uint16_t default_waiter_slots = 64;
inline constexpr std::uint32_t record_alignment = 64;
inline constexpr std::uint32_t page_size = 4096;
inline constexpr double max_timeout = 86400.0;
inline constexpr double default_lock_timeout = 5.0;
inline constexpr std::uint32_t kind_shift = 24;
inline constexpr std::uint32_t capacity_mask = (std::uint32_t{1} << kind_shift) - 1;
// A description's offset is the low 24 bits of a field entry, so the table holds at most 16 MiB.
inline constexpr std::uint32_t max_types_size = capacity_mask + 1;
inline constexpr std::uint32_t max_type_depth = 16;
// Layout 2.0 keeps the page-rounded mapping below 4 GiB, so its size fits header::size.
inline constexpr std::uint64_t max_mapping_size = (std::uint64_t{1} << 32) - page_size;

inline constexpr std::uint8_t kind_bool = 0;
inline constexpr std::uint8_t kind_int = 1;
inline constexpr std::uint8_t kind_float = 2;
inline constexpr std::uint8_t kind_str = 3;
inline constexpr std::uint8_t kind_bytes = 4;
inline constexpr std::uint8_t kind_ref = 5;
inline constexpr std::uint8_t kind_complex = 6;
inline constexpr std::uint8_t kind_date = 7;
inline constexpr std::uint8_t kind_time = 8;
inline constexpr std::uint8_t kind_datetime = 9;
inline constexpr std::uint8_t kind_timedelta = 10;
inline constexpr std::uint8_t kind_uuid = 11;
inline constexpr std::uint8_t kind_decimal = 12;
// From here on a field entry's low 24 bits are the offset of the field's description, not a size.
inline constexpr std::uint8_t first_described_kind = 64;
inline constexpr std::uint8_t kind_enum = 64;
inline constexpr std::uint8_t kind_flag = 65;
inline constexpr std::uint8_t kind_literal = 66;
inline constexpr std::uint8_t kind_optional = 67;
inline constexpr std::uint8_t kind_union = 68;
inline constexpr std::uint8_t kind_record = 69;
inline constexpr std::uint8_t kind_tuple = 70;
inline constexpr std::uint8_t kind_list = 71;
inline constexpr std::uint8_t kind_set = 72;
inline constexpr std::uint8_t kind_dict = 73;
inline constexpr std::uint8_t kind_array = 74;

enum class status : int {
    ok = 0,
    exists = -1,
    not_found = -2,
    layout = -3,
    schema = -4,
    corrupt = -5,
    lock_timeout = -6,
    timeout = -7,
    no_slot = -8,
    range = -9,
    os = -10,
    kind_mismatch = -11,
};

// A field as create takes it and field() returns it; kind is one of the kind_ constants.
struct field_spec {
    std::uint32_t offset;
    std::uint32_t capacity;
    std::uint8_t kind;
};

// Bytes for one field in the record encoding, without the length prefix of str, bytes and decimal.
struct value {
    std::uint16_t field;
    std::span<const std::byte> bytes;
};

using seconds = std::chrono::duration<double>;

// len is the field's stored length; when it exceeds the buffer, nothing was copied.
struct read_value {
    std::size_t len;
    std::uint64_t version;
};

// An array field held for writing in place: its bytes in the record, and the even seq begin_write locked
// from, which end_write releases.
struct field_write {
    std::uint16_t field;
    std::span<std::byte> bytes;
    std::uint64_t locked;
};

enum class wake { changed, interrupted };

struct header {
    std::uint64_t magic;
    std::uint16_t layout_major;
    std::uint16_t layout_minor;
    std::uint16_t field_count;
    std::uint16_t waiter_slots;
    std::uint64_t schema_hash;
    std::uint32_t record_size;
    std::uint32_t record;
    std::uint32_t tail;
    std::uint32_t size;
    std::uint64_t create_id;
    std::uint64_t creator_start;
    std::uint32_t creator_pid;
    // Bytes of the description table after the waiter slots; 0 in layout 1.0, where these were reserved.
    std::uint32_t types_size;
    std::uint64_t seq;
    std::uint32_t writer_pid;
    std::uint32_t wake_word;
    std::uint32_t waiters;
    std::uint8_t pad0[4];
    std::uint64_t creator_pidns;
    std::uint8_t reserved1[32];
};

struct stored_field {
    std::uint32_t offset;
    std::uint32_t capacity_and_kind;
};

struct waiter_slot {
    std::uint64_t owner_start;
    std::uint64_t owner_pidns;
    std::uint32_t owner_pid;
    std::uint32_t interrupt;
};

// The stored value of a kind_ref field: which box it refers to. All zero when empty; create never gives
// a create_id of 0. name is NUL-padded and ends at the first NUL or after name_max bytes.
struct box_ref {
    std::uint64_t create_id;
    std::uint64_t schema_hash;
    char name[name_max];
};

// The head of one description in the table of a layout 2.0 segment. A description is this head, a body
// that depends on kind, and zero padding to a multiple of 8 bytes.
struct type_head {
    std::uint8_t kind;
    std::uint8_t flags;
    std::uint16_t count;
    std::uint32_t size;
};

// The DLPack type of an array field's elements.
struct dl_dtype {
    std::uint8_t code;
    std::uint8_t bits;
    std::uint16_t lanes;
};

inline constexpr std::uint8_t literal_none = 0;
inline constexpr std::uint8_t literal_bool = 1;
inline constexpr std::uint8_t literal_int = 2;
inline constexpr std::uint8_t literal_str = 3;
inline constexpr std::uint8_t literal_bytes = 4;
inline constexpr std::uint8_t literal_enum = 5;

// One value a literal field can hold: integer for bool and int, text for str, bytes and an enum member's
// name. text points into the handle's copy of the table.
struct literal_value {
    std::uint8_t tag;
    std::int64_t integer;
    std::string_view text;
};

static_assert(sizeof(type_head) == 8 && alignof(type_head) == 4);
static_assert(offsetof(type_head, flags) == 1 && offsetof(type_head, count) == 2 &&
              offsetof(type_head, size) == 4);
static_assert(sizeof(dl_dtype) == 4);

static_assert(std::is_standard_layout_v<header> && std::is_trivially_copyable_v<header>);
static_assert(std::is_standard_layout_v<stored_field> && std::is_trivially_copyable_v<stored_field>);
static_assert(std::is_standard_layout_v<waiter_slot> && std::is_trivially_copyable_v<waiter_slot>);
static_assert(std::is_standard_layout_v<box_ref> && std::is_trivially_copyable_v<box_ref>);
static_assert(std::is_standard_layout_v<sbx_handle> && std::is_trivially_copyable_v<sbx_handle>);
static_assert(sizeof(header) == 128 && alignof(header) == 8);
static_assert(offsetof(header, magic) == 0);
static_assert(offsetof(header, layout_major) == 8);
static_assert(offsetof(header, layout_minor) == 10);
static_assert(offsetof(header, field_count) == 12);
static_assert(offsetof(header, waiter_slots) == 14);
static_assert(offsetof(header, schema_hash) == 16);
static_assert(offsetof(header, record_size) == 24);
static_assert(offsetof(header, record) == 28);
static_assert(offsetof(header, tail) == 32);
static_assert(offsetof(header, size) == 36);
static_assert(offsetof(header, create_id) == 40);
static_assert(offsetof(header, creator_start) == 48);
static_assert(offsetof(header, creator_pid) == 56);
static_assert(offsetof(header, types_size) == 60);
static_assert(offsetof(header, seq) == 64);
static_assert(offsetof(header, writer_pid) == 72);
static_assert(offsetof(header, wake_word) == 76);
static_assert(offsetof(header, waiters) == 80);
static_assert(offsetof(header, pad0) == 84);
static_assert(offsetof(header, creator_pidns) == 88);
static_assert(offsetof(header, reserved1) == 96);
static_assert(sizeof(stored_field) == 8 && alignof(stored_field) == 4);
static_assert(offsetof(stored_field, offset) == 0);
static_assert(offsetof(stored_field, capacity_and_kind) == 4);
static_assert(sizeof(waiter_slot) == 24 && alignof(waiter_slot) == 8);
static_assert(offsetof(waiter_slot, owner_start) == 0);
static_assert(offsetof(waiter_slot, owner_pidns) == 8);
static_assert(offsetof(waiter_slot, owner_pid) == 16);
static_assert(offsetof(waiter_slot, interrupt) == 20);
static_assert(sizeof(box_ref) == 144 && alignof(box_ref) == 8);
static_assert(offsetof(box_ref, create_id) == 0);
static_assert(offsetof(box_ref, schema_hash) == 8);
static_assert(offsetof(box_ref, name) == 16);
static_assert(sizeof(sbx_handle) == 48 && alignof(sbx_handle) == 8);
static_assert(offsetof(sbx_handle, layout_major) == 0);
static_assert(offsetof(sbx_handle, layout_minor) == 2);
static_assert(offsetof(sbx_handle, handle_version) == 4);
static_assert(offsetof(sbx_handle, base) == 8);
static_assert(offsetof(sbx_handle, size) == 16);
static_assert(offsetof(sbx_handle, name) == 24);
static_assert(offsetof(sbx_handle, release) == 32);
static_assert(offsetof(sbx_handle, private_data) == 40);

// Only lock-free atomics work on memory shared between processes: a fallback that takes a lock would
// keep that lock in one process. With Clang's libc++, std::atomic_ref needs libc++ 19 or later.
static_assert(std::atomic_ref<std::uint32_t>::is_always_lock_free);
static_assert(std::atomic_ref<std::uint64_t>::is_always_lock_free);
namespace detail {
inline constexpr std::size_t align32 = std::atomic_ref<std::uint32_t>::required_alignment;
inline constexpr std::size_t align64 = std::atomic_ref<std::uint64_t>::required_alignment;
} // namespace detail
static_assert(offsetof(header, magic) % detail::align64 == 0 && offsetof(header, seq) % detail::align64 == 0);
static_assert(offsetof(header, writer_pid) % detail::align32 == 0 &&
              offsetof(header, wake_word) % detail::align32 == 0);
static_assert(offsetof(header, waiters) % detail::align32 == 0);
static_assert(offsetof(waiter_slot, owner_start) % detail::align64 == 0 &&
              offsetof(waiter_slot, owner_pidns) % detail::align64 == 0);
static_assert(offsetof(waiter_slot, owner_pid) % detail::align32 == 0 &&
              offsetof(waiter_slot, interrupt) % detail::align32 == 0);
// The write counts start at header_size + field_count * sizeof(stored_field), the slots after them.
static_assert(header_size % detail::align64 == 0 && sizeof(stored_field) % detail::align64 == 0);
static_assert(sizeof(waiter_slot) % detail::align64 == 0 && page_size % alignof(header) == 0);

static_assert(SBX_OK == int(status::ok) && SBX_E_EXISTS == int(status::exists));
static_assert(SBX_E_NOT_FOUND == int(status::not_found) && SBX_E_LAYOUT == int(status::layout));
static_assert(SBX_E_SCHEMA == int(status::schema) && SBX_E_CORRUPT == int(status::corrupt));
static_assert(SBX_E_LOCK_TIMEOUT == int(status::lock_timeout) && SBX_E_TIMEOUT == int(status::timeout));
static_assert(SBX_E_NO_SLOT == int(status::no_slot) && SBX_E_RANGE == int(status::range));
static_assert(SBX_E_OS == int(status::os) && SBX_E_KIND == int(status::kind_mismatch));

// Why a call failed: the status, the OS error (errno or GetLastError()) read where the OS call failed,
// and what the call found when that is the reason: the magic for kind_mismatch, major << 16 | minor
// for layout.
struct error {
    status code = status::ok;
    std::int32_t os = 0;
    std::uint64_t found = 0;
};
static_assert(sizeof(error) == 16 && std::is_trivially_copyable_v<error>);

// The error of a result, as std::unexpected<error> is on C++23.
class unexpected {
public:
    constexpr explicit unexpected(status code) noexcept : error_{code, 0, 0} {}
    constexpr explicit unexpected(sharedbox::error e) noexcept : error_(e) {}
    constexpr const sharedbox::error &error() const noexcept { return error_; }

private:
    sharedbox::error error_;
};

template <class T> class result;
template <> class result<void>;

namespace detail {
template <class> inline constexpr bool is_result = false;
template <class T> inline constexpr bool is_result<result<T>> = true;
} // namespace detail

// A value or an error, with the part of the interface of std::expected<T, error> this library
// uses. value() on an error terminates, where std::expected would throw.
template <class T> class result {
public:
    using value_type = T;
    using error_type = sharedbox::error;

    template <class U = T>
        requires(std::is_constructible_v<T, U> && !std::is_same_v<std::remove_cvref_t<U>, result> &&
                 !std::is_same_v<std::remove_cvref_t<U>, unexpected> &&
                 !(std::is_same_v<std::remove_cv_t<T>, bool> && detail::is_result<std::remove_cvref_t<U>>))
    constexpr explicit(!std::is_convertible_v<U, T>) result(U &&v)
        : v_(std::in_place_index<0>, std::forward<U>(v)) {}
    constexpr result(unexpected e) : v_(std::in_place_index<1>, e) {}
#if defined(__cpp_lib_expected) && __cpp_lib_expected >= 202211L
    // result never aliases std::expected, so a program may mix translation units built as C++20 and as
    // C++23; to_expected converts the other way.
    constexpr result(std::expected<T, sharedbox::error> e)
        : result(e ? result(std::move(*e)) : result(unexpected(e.error()))) {}
#endif

    constexpr bool has_value() const noexcept { return v_.index() == 0; }
    constexpr explicit operator bool() const noexcept { return has_value(); }
    constexpr T &operator*() & noexcept { return *std::get_if<0>(&v_); }
    constexpr const T &operator*() const & noexcept { return *std::get_if<0>(&v_); }
    constexpr T &&operator*() && noexcept { return std::move(*std::get_if<0>(&v_)); }
    constexpr T *operator->() noexcept { return std::get_if<0>(&v_); }
    constexpr const T *operator->() const noexcept { return std::get_if<0>(&v_); }
    constexpr T &value() & { return checked(), **this; }
    constexpr const T &value() const & { return checked(), **this; }
    constexpr T &&value() && { return checked(), std::move(**this); }
    constexpr const sharedbox::error &error() const noexcept { return std::get_if<1>(&v_)->error(); }
    template <class U> constexpr T value_or(U &&fallback) const & {
        static_assert(std::is_convertible_v<U, T>, "sharedbox::result: value_or needs a value convertible to T");
        return has_value() ? **this : static_cast<T>(std::forward<U>(fallback));
    }
    template <class U> constexpr T value_or(U &&fallback) && {
        static_assert(std::is_convertible_v<U, T>, "sharedbox::result: value_or needs a value convertible to T");
        return has_value() ? std::move(**this) : static_cast<T>(std::forward<U>(fallback));
    }
    template <class F> constexpr auto and_then(F &&f) & { return then(*this, std::forward<F>(f)); }
    template <class F> constexpr auto and_then(F &&f) const & { return then(*this, std::forward<F>(f)); }
    template <class F> constexpr auto and_then(F &&f) && { return then(std::move(*this), std::forward<F>(f)); }
    template <class F> constexpr auto transform(F &&f) & { return map(*this, std::forward<F>(f)); }
    template <class F> constexpr auto transform(F &&f) const & { return map(*this, std::forward<F>(f)); }
    template <class F> constexpr auto transform(F &&f) && { return map(std::move(*this), std::forward<F>(f)); }
    template <class F> constexpr result or_else(F &&f) const & {
        static_assert(
            std::is_same_v<std::remove_cvref_t<std::invoke_result_t<F, const sharedbox::error &>>, result>,
            "sharedbox::result: or_else must return the same result type");
        return has_value() ? result(**this) : std::forward<F>(f)(error());
    }
    template <class F> constexpr result or_else(F &&f) && {
        static_assert(
            std::is_same_v<std::remove_cvref_t<std::invoke_result_t<F, const sharedbox::error &>>, result>,
            "sharedbox::result: or_else must return the same result type");
        return has_value() ? result(std::move(**this)) : std::forward<F>(f)(error());
    }

private:
    constexpr void checked() const {
        if (!has_value())
            std::terminate();
    }
    template <class Self, class F> static constexpr auto then(Self &&self, F &&f) {
        using R = std::remove_cvref_t<std::invoke_result_t<F, decltype(*std::forward<Self>(self))>>;
        if (self.has_value())
            return std::forward<F>(f)(*std::forward<Self>(self));
        return R(unexpected(self.error()));
    }
    template <class Self, class F> static constexpr auto map(Self &&self, F &&f) {
        using U = std::remove_cv_t<std::invoke_result_t<F, decltype(*std::forward<Self>(self))>>;
        static_assert(!std::is_reference_v<U>, "sharedbox::result: transform must not return a reference");
        if constexpr (std::is_void_v<U>) {
            if (!self.has_value())
                return result<U>(unexpected(self.error()));
            std::forward<F>(f)(*std::forward<Self>(self));
            return result<U>();
        } else {
            if (!self.has_value())
                return result<U>(unexpected(self.error()));
            return result<U>(std::forward<F>(f)(*std::forward<Self>(self)));
        }
    }

    std::variant<T, unexpected> v_;
};

template <> class result<void> {
public:
    using value_type = void;
    using error_type = sharedbox::error;

    constexpr result() noexcept = default;
    constexpr result(unexpected e) noexcept : has_value_(false), error_(e.error()) {}
#if defined(__cpp_lib_expected) && __cpp_lib_expected >= 202211L
    // result never aliases std::expected, so a program may mix translation units built as C++20 and as
    // C++23; to_expected converts the other way.
    constexpr result(std::expected<void, sharedbox::error> e)
        : result(e ? result() : result(unexpected(e.error()))) {}
#endif

    constexpr bool has_value() const noexcept { return has_value_; }
    constexpr explicit operator bool() const noexcept { return has_value(); }
    constexpr void operator*() const noexcept {}
    constexpr void value() const {
        if (!has_value())
            std::terminate();
    }
    constexpr const sharedbox::error &error() const noexcept { return error_; }
    template <class F> constexpr auto and_then(F &&f) const {
        using R = std::remove_cvref_t<std::invoke_result_t<F>>;
        if (has_value())
            return std::forward<F>(f)();
        return R(unexpected(error_));
    }
    template <class F> constexpr auto transform(F &&f) const {
        using U = std::remove_cv_t<std::invoke_result_t<F>>;
        static_assert(!std::is_reference_v<U>, "sharedbox::result: transform must not return a reference");
        if constexpr (std::is_void_v<U>) {
            if (has_value())
                std::forward<F>(f)();
            return *this;
        } else {
            if (!has_value())
                return result<U>(unexpected(error_));
            return result<U>(std::forward<F>(f)());
        }
    }
    template <class F> constexpr result or_else(F &&f) const {
        static_assert(
            std::is_same_v<std::remove_cvref_t<std::invoke_result_t<F, const sharedbox::error &>>, result>,
            "sharedbox::result: or_else must return the same result type");
        return has_value() ? *this : std::forward<F>(f)(error_);
    }

private:
    bool has_value_ = true;
    sharedbox::error error_{};
};

#if defined(__cpp_lib_expected) && __cpp_lib_expected >= 202211L
// A free function because expected's own converting constructor would read a result<bool> through its
// explicit operator bool, and a member conversion cannot move a move-only value.
template <class T> constexpr std::expected<T, error> to_expected(result<T> r) {
    if (!r)
        return std::unexpected(r.error());
    if constexpr (std::is_void_v<T>)
        return {};
    else
        return *std::move(r);
}
#endif

namespace detail {

// Shared words are plain integers in the mapping; every access to them goes through std::atomic_ref.
template <class T> SHAREDBOX_HOT std::atomic_ref<T> atomic(T &word) noexcept { return std::atomic_ref<T>(word); }

} // namespace detail

namespace detail {

struct process_cache {
    std::atomic<std::uint32_t> pid{0};
    std::atomic<std::uint64_t> start{0};
    // The namespace plus one: 0 until it is read, 1 when it cannot be.
    std::atomic<std::uint64_t> pidns{0};
};

// False once pthread_atfork could not be registered, which makes every cached value unsafe across an
// unnoticed fork; the accessors then read everything fresh instead of trusting the cache.
inline std::atomic<bool> atfork_registered{true};

inline process_cache &cache() noexcept;

#ifndef _WIN32
inline void after_fork() noexcept {
    process_cache &c = cache();
    c.pid.store(static_cast<std::uint32_t>(getpid()), std::memory_order_relaxed);
    c.start.store(0, std::memory_order_relaxed);
    c.pidns.store(0, std::memory_order_relaxed);
}

inline std::uint64_t read_pidns() noexcept {
    struct stat st;
    return stat("/proc/self/ns/pid", &st) == 0 ? static_cast<std::uint64_t>(st.st_ino) : 0;
}
#endif

// Every accessor goes through here, so the fork handler is registered before the first value is
// cached; the static's initialisation runs once per program, under the compiler's guard.
inline process_cache &cache() noexcept {
    static process_cache c = [] {
#ifndef _WIN32
        if (pthread_atfork(nullptr, nullptr, after_fork) != 0)
            atfork_registered.store(false, std::memory_order_relaxed);
#endif
        return process_cache{};
    }();
    return c;
}

#ifndef _WIN32
// Runs the cache's first initialisation while the executable or shared library that includes this
// header is loaded, before its code can call the accessors. Otherwise a fork while another thread is
// inside that initialisation would leave the child stopped on the compiler-generated guard, which
// only the (now absent) initialising thread would clear. For an executable this is before main; a
// library loaded with dlopen runs it inside dlopen, where other threads may already exist, so a fork
// from one of them during that call is not covered.
inline const bool process_cache_ready = (cache(), true);
#endif

// What process_start returns for a process that exists but cannot be inspected.
inline constexpr std::uint64_t start_unknown = UINT64_MAX;

// This process's pid. On Linux it is read once and again in every child created by fork.
inline std::uint32_t current_pid() noexcept {
#ifdef _WIN32
    return static_cast<std::uint32_t>(GetCurrentProcessId());
#else
    if (!atfork_registered.load(std::memory_order_relaxed))
        return static_cast<std::uint32_t>(getpid());
    process_cache &c = cache();
    std::uint32_t pid = c.pid.load(std::memory_order_relaxed);
    if (pid == 0) {
        pid = static_cast<std::uint32_t>(getpid());
        c.pid.store(pid, std::memory_order_relaxed);
    }
    return pid;
#endif
}

// When the process started, in an OS-specific unit: 0 when no running process has that pid,
// start_unknown when one does but its start time cannot be read.
inline std::uint64_t process_start(std::uint32_t pid) noexcept {
#ifdef _WIN32
    HANDLE process = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (process == nullptr)
        return GetLastError() == ERROR_ACCESS_DENIED ? start_unknown : 0;
    std::uint64_t start = 0;
    // An exited process stays queryable while a handle to it is open, and its exit code may be any
    // value, STILL_ACTIVE included; only the handle's signal state is reliable.
    if (WaitForSingleObject(process, 0) == WAIT_TIMEOUT) {
        FILETIME created, exited, kernel, user;
        start = GetProcessTimes(process, &created, &exited, &kernel, &user)
                    ? (std::uint64_t{created.dwHighDateTime} << 32) | created.dwLowDateTime
                    : start_unknown;
    }
    CloseHandle(process);
    return start;
#else
    static_assert(sizeof(pid_t) == sizeof(int));
    // kill(0, ...) and negative pids address process groups, not one process.
    if (pid == 0 || pid > static_cast<std::uint32_t>(INT_MAX))
        return 0;
    bool denied = false;
    if (kill(static_cast<pid_t>(pid), 0) != 0) {
        if (errno == ESRCH)
            return 0;
        denied = errno == EPERM;
    }
    char path[32];
    std::snprintf(path, sizeof path, "/proc/%u/stat", static_cast<unsigned>(pid));
    const int fd = open(path, O_RDONLY | O_CLOEXEC);
    const int open_errno = errno;
    // /proc mounted with hidepid hides processes of other users, which kill reports as EPERM. Any
    // other reason open can fail (EMFILE, ENFILE, ENOMEM, ...) means the process could not be
    // inspected, not that it is gone.
    if (fd < 0)
        return (denied || (open_errno != ENOENT && open_errno != ESRCH)) ? start_unknown : 0;
    char text[1024];
    const ssize_t n = read(fd, text, sizeof text - 1);
    close(fd);
    // kill has found the process, so a stat that cannot be parsed means an unknown start time.
    if (n <= 0)
        return start_unknown;
    text[n] = '\0';
    // Field 2, the command name, may hold spaces and parentheses; the fixed fields follow the last ')'.
    const char *s = std::strrchr(text, ')');
    if (s == nullptr)
        return start_unknown;
    ++s;
    while (*s == ' ')
        ++s;
    if (*s == '\0')
        return start_unknown;
    // Field 3 is the state; a zombie has exited but keeps its /proc entry until it is reaped.
    if (*s == 'Z' || *s == 'X')
        return 0;
    for (int field = 3; field < 22; ++field) {
        while (*s != '\0' && *s != ' ')
            ++s;
        while (*s == ' ')
            ++s;
    }
    if (*s < '0' || *s > '9')
        return start_unknown;
    std::uint64_t start = 0;
    while (*s >= '0' && *s <= '9')
        start = start * 10 + static_cast<std::uint64_t>(*s++ - '0');
    // 0 means no process, so a process started at boot tick 0 reports 1.
    return start == 0 ? 1 : start;
#endif
}

// This process's start time, read on first use.
inline std::uint64_t current_start() noexcept {
#ifndef _WIN32
    if (!atfork_registered.load(std::memory_order_relaxed))
        return process_start(current_pid());
#endif
    process_cache &c = cache();
    std::uint64_t start = c.start.load(std::memory_order_relaxed);
    if (start == 0) {
        start = process_start(current_pid());
        c.start.store(start, std::memory_order_relaxed);
    }
    return start;
}

// The inode number of /proc/self/ns/pid, which tells pid namespaces apart. 0 means unknown: on Linux
// when /proc cannot be read, and always on Windows, which has no pid namespaces.
inline std::uint64_t current_pidns() noexcept {
#ifdef _WIN32
    return 0;
#else
    if (!atfork_registered.load(std::memory_order_relaxed))
        return read_pidns();
    process_cache &c = cache();
    std::uint64_t cached = c.pidns.load(std::memory_order_relaxed);
    if (cached == 0) {
        cached = read_pidns() + 1;
        c.pidns.store(cached, std::memory_order_relaxed);
    }
    return cached - 1;
#endif
}

// False when no process has that pid, or one does with another start time. A process whose start
// time cannot be read counts as alive, so nothing is freed on a guess.
inline bool process_alive(std::uint32_t pid, std::uint64_t start) noexcept {
    if (pid == 0)
        return false;
    const std::uint64_t now = process_start(pid);
    return now != 0 && (now == start_unknown || start == start_unknown || now == start);
}

inline constexpr std::uint32_t max_record_size = max_fields * (max_capacity + 8u);
inline constexpr std::size_t object_name_max = 160;
inline constexpr int futex_wait = 0;
inline constexpr int futex_wake = 1;

using clock = std::chrono::steady_clock;

SHAREDBOX_HOT void cpu_relax() noexcept {
#if defined(_WIN32)
    YieldProcessor();
#elif defined(__x86_64__) || defined(__i386__)
    __builtin_ia32_pause();
#elif defined(__aarch64__)
    __asm__ __volatile__("yield");
#endif
}

class backoff {
public:
    explicit backoff(double timeout) noexcept : timeout_(timeout) {}

    // The clock is read only after the first failed attempt, so an uncontended call makes no clock call.
    bool expired() noexcept {
        const clock::time_point now = clock::now();
        if (!started_) {
            started_ = true;
            deadline_ = now + std::chrono::duration_cast<clock::duration>(seconds(timeout_));
            return false;
        }
        return now >= deadline_;
    }

    // Spins, then yields, then sleeps for a doubling interval capped at 1 ms, so a wait on a creator that
    // never finishes does not keep a core busy for the whole timeout.
    void pause() noexcept {
        ++spins_;
        if (spins_ < 64) {
            cpu_relax();
        } else if (spins_ < 128) {
            std::this_thread::yield();
        } else {
            std::this_thread::sleep_for(sleep_);
            // The parentheses stop a min macro, from windows.h included earlier without NOMINMAX.
            sleep_ = (std::min)(sleep_ * 2, std::chrono::microseconds(1000));
        }
    }

private:
    double timeout_;
    clock::time_point deadline_{};
    std::chrono::microseconds sleep_{10};
    unsigned spins_ = 0;
    bool started_ = false;
};

inline bool timeout_ok(seconds t, bool zero_allowed) noexcept {
    const double s = t.count();
    return (zero_allowed ? s >= 0 : s > 0) && s <= max_timeout;
}

inline bool described(std::uint32_t kind) noexcept { return kind >= first_described_kind; }

// str, bytes and decimal: a u32 length, then up to capacity bytes. A mask, so the int path of payload and
// store pays one test, as it did when only str and bytes were prefixed.
inline bool prefixed(std::uint32_t kind) noexcept {
    constexpr std::uint32_t mask = (1u << kind_str) | (1u << kind_bytes) | (1u << kind_decimal);
    return kind < 32 && ((mask >> kind) & 1u) != 0;
}

inline bool kind_known(std::uint32_t kind) noexcept {
    return kind <= kind_decimal || (kind >= kind_enum && kind <= kind_array);
}

// Alignment of a fixed kind within the record; 1 for a kind this header does not know, whose alignment
// it cannot check. A described kind's alignment comes from its description.
inline std::uint32_t kind_alignment(std::uint32_t kind) noexcept {
    switch (kind) {
    case kind_int:
    case kind_float:
    case kind_ref:
    case kind_complex:
    case kind_time:
    case kind_datetime:
        return 8u;
    case kind_str:
    case kind_bytes:
    case kind_decimal:
    case kind_date:
    case kind_timedelta:
        return 4u;
    default:
        return 1u;
    }
}

// The capacity every field of a fixed-size kind has; 0 for prefixed, described and unknown kinds.
inline std::uint32_t fixed_capacity(std::uint32_t kind) noexcept {
    switch (kind) {
    case kind_bool:
        return 1u;
    case kind_int:
    case kind_float:
        return 8u;
    case kind_ref:
        return sizeof(box_ref);
    case kind_complex:
    case kind_time:
    case kind_datetime:
    case kind_uuid:
        return 16u;
    case kind_date:
        return 4u;
    case kind_timedelta:
        return 12u;
    default:
        return 0u;
    }
}

// Bytes the field occupies in the record, the length prefix of str, bytes and decimal included.
inline std::uint64_t field_span(const field_spec &f) noexcept {
    return (prefixed(f.kind) ? 4u : 0u) + std::uint64_t{f.capacity};
}

// Whether any two fields share a byte of the record; at most max_fields, so pairs are compared.
inline bool fields_overlap(std::span<const field_spec> fields) noexcept {
    for (std::size_t i = 0; i < fields.size(); ++i)
        for (std::size_t j = i + 1; j < fields.size(); ++j)
            if (fields[i].offset < fields[j].offset + field_span(fields[j]) &&
                fields[j].offset < fields[i].offset + field_span(fields[i]))
                return true;
    return false;
}

inline std::uint64_t round_up(std::uint64_t value, std::uint64_t unit) noexcept {
    return (value + unit - 1) / unit * unit;
}

// Where the waiter slots end and the padding before the record starts.
inline std::uint64_t tail_end(std::uint32_t field_count, std::uint32_t waiter_slots) noexcept {
    return header_size + std::uint64_t{field_count} * (sizeof(stored_field) + sizeof(std::uint64_t)) +
           std::uint64_t{waiter_slots} * sizeof(waiter_slot);
}

inline constexpr std::uint32_t no_node = UINT32_MAX;

// The type of a field or member: its kind, the bytes and alignment it takes in the record, a length prefix
// included, and for a described kind the node holding the rest.
struct type_ref {
    std::uint8_t kind = 0;
    std::uint32_t size = 0;
    std::uint32_t alignment = 1;
    std::uint32_t node = no_node;
};

struct type_member {
    std::uint32_t offset = 0;
    type_ref type;
};

struct type_node {
    std::uint32_t at = 0;
    std::uint32_t length = 0;
    std::uint16_t count = 0;
    std::uint32_t members = 0;
    std::uint32_t names = 0;
    std::uint32_t numbers = 0;
    std::uint32_t literals = 0;
    std::uint32_t capacity = 0;
    std::uint32_t slots = 0;
    std::uint32_t stride = 0;
    dl_dtype dtype{};
    std::uint8_t ndim = 0;
};

struct type_counts {
    std::uint32_t nodes = 0;
    std::uint32_t members = 0;
    std::uint32_t names = 0;
    std::uint32_t numbers = 0;
    std::uint32_t literals = 0;
};

// Whether text is well-formed UTF-8: no overlong forms, no surrogates, nothing above U+10FFFF.
inline bool utf8_ok(std::string_view text) noexcept {
    std::size_t i = 0;
    while (i < text.size()) {
        const auto c = static_cast<unsigned char>(text[i]);
        const std::size_t n = c < 0x80                   ? 1
                              : (c >= 0xC2 && c <= 0xDF) ? 2
                              : (c >> 4) == 0xE          ? 3
                              : (c >= 0xF0 && c <= 0xF4) ? 4
                                                         : 0;
        if (n == 0 || text.size() - i < n)
            return false;
        for (std::size_t k = 1; k < n; ++k)
            if ((static_cast<unsigned char>(text[i + k]) & 0xC0) != 0x80)
                return false;
        if (n > 2) {
            const auto b = static_cast<unsigned char>(text[i + 1]);
            if ((c == 0xE0 && b < 0xA0) || (c == 0xED && b > 0x9F) || (c == 0xF0 && b < 0x90) ||
                (c == 0xF4 && b > 0x8F))
                return false;
        }
        i += n;
    }
    return true;
}

class type_parser;

// A checked copy of a segment's description table: each field's type and every description it reaches,
// fixed once parse returns. Movable, not copyable; copy() parses the same bytes into another table.
class type_table {
public:
    // Copies table and checks it with the field entries, capacity_and_kind as stored. With v1 every entry
    // is read as a 1.0 field and the table must be empty. On failure the table is left empty.
    status parse(std::span<const std::byte> table, std::span<const std::uint32_t> entries, bool v1) noexcept;
    status copy(type_table &out) const noexcept { return out.parse(bytes(), {entries_.get(), field_count_}, v1_); }

    std::span<const std::byte> bytes() const noexcept { return {bytes_.get(), size_}; }
    std::uint32_t node_count() const noexcept { return node_count_; }
    const type_ref &field(std::uint16_t i) const noexcept { return fields_[i]; }
    const type_node &node(std::uint32_t i) const noexcept { return nodes_[i]; }
    const type_member &member(std::uint32_t i) const noexcept { return members_[i]; }
    std::string_view name(std::uint32_t i) const noexcept { return names_[i]; }
    std::uint64_t number(std::uint32_t i) const noexcept { return numbers_[i]; }
    const std::uint64_t &numbers_view(std::uint32_t i) const noexcept { return numbers_[i]; }
    const literal_value &literal(std::uint32_t i) const noexcept { return literals_[i]; }

private:
    friend class type_parser;
    std::unique_ptr<std::byte[]> bytes_;
    std::uint32_t size_ = 0;
    std::unique_ptr<std::uint32_t[]> entries_;
    std::uint16_t field_count_ = 0;
    std::uint32_t node_count_ = 0;
    bool v1_ = false;
    std::unique_ptr<type_ref[]> fields_;
    std::unique_ptr<type_node[]> nodes_;
    std::unique_ptr<type_member[]> members_;
    std::unique_ptr<std::string_view[]> names_;
    std::unique_ptr<std::uint64_t[]> numbers_;
    std::unique_ptr<literal_value[]> literals_;
};

// Walks the descriptions the field entries reach and checks each. With out null it only counts, so the
// table's arrays can be sized; the second walk, in the same order, fills them and checks the layout of
// members, which needs their types.
class type_parser {
public:
    type_parser(std::span<const std::byte> table, std::uint64_t *seen, type_table *out) noexcept
        : table_(table), seen_(seen), out_(out) {}

    type_counts counts;

    status field(std::uint32_t entry, bool v1, type_ref &out) noexcept {
        const auto kind = static_cast<std::uint8_t>(entry >> kind_shift);
        const std::uint32_t low = entry & capacity_mask;
        if (v1 && described(kind) && kind_known(kind))
            return status::corrupt;
        if (v1 || !described(kind))
            return fixed(kind, low, true, out);
        return describe(kind, low, no_parent, 1, true, true, out);
    }

private:
    static constexpr std::uint32_t no_parent = UINT32_MAX;

    struct cursor {
        const std::byte *at;
        const std::byte *end;
        bool ok = true;

        template <class T> T take() noexcept {
            T value{};
            if (ok && static_cast<std::size_t>(end - at) >= sizeof value) {
                std::memcpy(&value, at, sizeof value);
                at += sizeof value;
            } else {
                ok = false;
            }
            return value;
        }
        std::string_view text(std::size_t n) noexcept {
            if (!ok || static_cast<std::size_t>(end - at) < n) {
                ok = false;
                return {};
            }
            const std::string_view out(reinterpret_cast<const char *>(at), n);
            at += n;
            return out;
        }
        std::string_view name() noexcept { return text(take<std::uint16_t>()); }
        void skip(std::size_t n) noexcept { static_cast<void>(text(n)); }
    };

    status fixed(std::uint8_t kind, std::uint32_t low, bool top, type_ref &out) noexcept {
        if (!kind_known(kind) || described(kind)) {
            // A field of a kind this header does not know keeps its bytes; a member cannot, since nothing
            // says where the next one starts.
            if (!top || low == 0 || low > max_capacity)
                return status::corrupt;
            out = {kind, low, 1, no_node};
            return status::ok;
        }
        if (kind == kind_ref && !top)
            return status::corrupt;
        if (prefixed(kind)) {
            if (low == 0 || low > max_capacity)
                return status::corrupt;
            out = {kind, 4 + low, 4, no_node};
            return status::ok;
        }
        if (low != fixed_capacity(kind))
            return status::corrupt;
        out = {kind, low, kind_alignment(kind), no_node};
        return status::ok;
    }

    // Marks the 8-byte units of [at, at + length) as read; false if one already was, which a description
    // referenced twice or two overlapping descriptions both cause.
    bool mark(std::uint32_t at, std::uint32_t length) noexcept {
        for (std::uint32_t unit = at / 8; unit < (at + length) / 8; ++unit) {
            std::uint64_t &word = seen_[unit / 64];
            const std::uint64_t bit = std::uint64_t{1} << (unit % 64);
            if ((word & bit) != 0)
                return false;
            word |= bit;
        }
        return true;
    }

    bool names(cursor &c, std::uint16_t count, std::uint32_t &first) noexcept {
        first = counts.names;
        counts.names += count;
        for (std::uint32_t i = 0; i < count; ++i) {
            const std::string_view name = c.name();
            if (!c.ok || name.empty() || !utf8_ok(name))
                return false;
            if (out_ != nullptr)
                out_->names_[first + i] = name;
        }
        return true;
    }

    bool literals(cursor &c, std::uint16_t count, std::uint32_t &first) noexcept {
        first = counts.literals;
        counts.literals += count;
        for (std::uint32_t i = 0; i < count; ++i) {
            literal_value value{c.take<std::uint8_t>(), 0, {}};
            switch (value.tag) {
            case literal_none:
                break;
            case literal_bool:
                value.integer = c.take<std::uint8_t>();
                if (value.integer > 1)
                    return false;
                break;
            case literal_int:
                value.integer = c.take<std::int64_t>();
                break;
            case literal_str:
            case literal_enum:
                value.text = c.name();
                if (!utf8_ok(value.text))
                    return false;
                break;
            case literal_bytes:
                value.text = c.text(c.take<std::uint32_t>());
                break;
            default:
                return false;
            }
            if (!c.ok)
                return false;
            if (out_ != nullptr)
                out_->literals_[first + i] = value;
        }
        return true;
    }

    status member(const std::byte *entry, std::uint32_t parent, std::uint32_t depth, bool array_ok,
                  type_member &out) noexcept {
        std::uint32_t value = 0;
        std::memcpy(&out.offset, entry, sizeof out.offset);
        std::memcpy(&value, entry + 4, sizeof value);
        const auto kind = static_cast<std::uint8_t>(value >> kind_shift);
        const std::uint32_t low = value & capacity_mask;
        if (!described(kind))
            return fixed(kind, low, false, out.type);
        return describe(kind, low, parent, depth + 1, false, array_ok, out.type);
    }

    status describe(std::uint8_t kind, std::uint32_t at, std::uint32_t parent, std::uint32_t depth, bool top,
                    bool array_ok, type_ref &out) noexcept;
    status place(std::uint8_t kind, const type_head &head, type_node &node, type_ref &out) noexcept;

    std::span<const std::byte> table_;
    std::uint64_t *seen_;
    type_table *out_;
};

inline status type_parser::describe(std::uint8_t kind, std::uint32_t at, std::uint32_t parent, std::uint32_t depth,
                                    bool top, bool array_ok, type_ref &out) noexcept {
    const std::size_t size = table_.size();
    // A child after its parent: with every description read once, the walk can never return to one.
    if (depth > max_type_depth || at % 8 != 0 || size < 8 || at > size - 8 ||
        (parent != no_parent && at <= parent))
        return status::corrupt;
    const std::byte *start = table_.data() + at;
    type_head head;
    std::memcpy(&head, start, sizeof head);
    if (head.kind != kind || head.size == 0)
        return status::corrupt;
    if (!kind_known(kind)) {
        if (!top || !mark(at, 8))
            return status::corrupt;
        out = {kind, head.size, 1, no_node};
        return status::ok;
    }
    if (head.flags != 0 || (kind == kind_array && !array_ok))
        return status::corrupt;
    cursor c{start + 8, table_.data() + size};
    const std::uint32_t index = counts.nodes++;
    type_node node{};
    node.at = at;
    node.count = head.count;
    out = {kind, head.size, 1, index};
    const std::byte *entries = nullptr;
    std::uint32_t entry_count = 0;
    bool members_take_arrays = false;
    bool ok = true;
    switch (kind) {
    case kind_enum:
        ok = head.count > 0 && head.size == 2 && names(c, head.count, node.names);
        break;
    case kind_flag:
        ok = head.count > 0 && head.count <= 64 && head.size == 8;
        node.names = counts.names;
        node.numbers = counts.numbers;
        counts.names += head.count;
        counts.numbers += head.count;
        for (std::uint32_t i = 0; ok && i < head.count; ++i) {
            const std::string_view name = c.name();
            const auto bits = c.take<std::uint64_t>();
            ok = c.ok && !name.empty() && utf8_ok(name);
            if (ok && out_ != nullptr) {
                out_->names_[node.names + i] = name;
                out_->numbers_[node.numbers + i] = bits;
            }
        }
        break;
    case kind_literal:
        ok = head.count > 0 && head.size == 2 && literals(c, head.count, node.literals);
        break;
    case kind_optional:
    case kind_union:
    case kind_record:
    case kind_tuple:
        if (kind == kind_optional)
            ok = head.count == 1;
        else if (kind == kind_union)
            ok = head.count >= 2 && head.count <= 255;
        else
            ok = head.count > 0 && head.count <= max_fields;
        entries = c.at;
        entry_count = head.count;
        c.skip(std::size_t{head.count} * 8);
        members_take_arrays = kind == kind_record || kind == kind_tuple;
        if (ok && kind == kind_record)
            ok = names(c, head.count, node.names);
        break;
    case kind_list:
    case kind_set:
    case kind_dict:
        ok = head.count == (kind == kind_dict ? 2 : 1);
        node.capacity = c.take<std::uint32_t>();
        ok = ok && node.capacity > 0 && node.capacity <= max_capacity;
        entries = c.at;
        entry_count = head.count;
        c.skip(std::size_t{head.count} * 8);
        break;
    case kind_array: {
        node.dtype = c.take<dl_dtype>();
        node.ndim = c.take<std::uint8_t>();
        const auto reserved0 = c.take<std::uint8_t>();
        const auto reserved1 = c.take<std::uint16_t>();
        // DLPack codes: 0 int, 1 uint, 2 float, 4 bfloat, 5 complex, 6 bool.
        const std::uint8_t code = node.dtype.code;
        ok = head.count == 0 && reserved0 == 0 && reserved1 == 0 && node.ndim >= 1 && node.ndim <= 8 &&
             (code <= 2 || (code >= 4 && code <= 6)) && node.dtype.bits > 0 && node.dtype.bits % 8 == 0 &&
             node.dtype.lanes == 1;
        node.numbers = counts.numbers;
        counts.numbers += node.ndim;
        std::uint64_t total = node.dtype.bits / 8;
        for (std::uint32_t i = 0; ok && i < node.ndim; ++i) {
            const auto dim = c.take<std::uint64_t>();
            // Each product stays below 2**32, so the next one cannot overflow 64 bits.
            ok = c.ok && dim > 0 && dim <= UINT32_MAX && (total *= dim) <= UINT32_MAX;
            if (ok && out_ != nullptr)
                out_->numbers_[node.numbers + i] = dim;
        }
        ok = ok && total == head.size;
        out.alignment = 64;
        break;
    }
    default:
        ok = false;
    }
    if (!ok || !c.ok)
        return status::corrupt;
    node.length = static_cast<std::uint32_t>(round_up(static_cast<std::uint64_t>(c.at - start), 8));
    // Marked before the members are read, so a member that points into its own parent is a second mark.
    if (!mark(at, node.length))
        return status::corrupt;
    node.members = counts.members;
    counts.members += entry_count;
    for (std::uint32_t i = 0; i < entry_count; ++i) {
        type_member m;
        if (const status rc = member(entries + std::size_t{i} * 8, at, depth, members_take_arrays, m);
            rc != status::ok)
            return rc;
        if (out_ != nullptr)
            out_->members_[node.members + i] = m;
    }
    if (out_ == nullptr)
        return status::ok;
    if (const status rc = place(kind, head, node, out); rc != status::ok)
        return rc;
    out_->nodes_[index] = node;
    return status::ok;
}

// Checks where the members of a filled node sit and sets the node's alignment, slots and stride. Runs on the
// second walk only, since it needs the members' types.
inline status type_parser::place(std::uint8_t kind, const type_head &head, type_node &node,
                                 type_ref &out) noexcept {
    const type_member *m = out_->members_.get() + node.members;
    const std::uint64_t size = head.size;
    switch (kind) {
    case kind_enum:
    case kind_literal:
        out.alignment = 2;
        return status::ok;
    case kind_flag:
        out.alignment = 8;
        return status::ok;
    case kind_array:
        return status::ok;
    case kind_optional: {
        const std::uint32_t a = m[0].type.alignment;
        out.alignment = a;
        return m[0].offset == a && size == round_up(a + std::uint64_t{m[0].type.size}, a) ? status::ok
                                                                                          : status::corrupt;
    }
    case kind_union: {
        std::uint32_t a = 1;
        std::uint64_t largest = 0;
        for (std::uint32_t i = 0; i < node.count; ++i) {
            a = (std::max)(a, m[i].type.alignment);
            largest = (std::max)(largest, std::uint64_t{m[i].type.size});
        }
        for (std::uint32_t i = 0; i < node.count; ++i)
            if (m[i].offset != a)
                return status::corrupt;
        out.alignment = a;
        return size == round_up(a + largest, a) ? status::ok : status::corrupt;
    }
    case kind_record:
    case kind_tuple: {
        std::uint32_t a = 1;
        for (std::uint32_t i = 0; i < node.count; ++i) {
            const type_member &x = m[i];
            a = (std::max)(a, x.type.alignment);
            if (x.offset % x.type.alignment != 0 || std::uint64_t{x.offset} + x.type.size > size)
                return status::corrupt;
            for (std::uint32_t j = 0; j < i; ++j)
                if (x.offset < std::uint64_t{m[j].offset} + m[j].type.size &&
                    m[j].offset < std::uint64_t{x.offset} + x.type.size)
                    return status::corrupt;
        }
        out.alignment = a;
        return size % a == 0 ? status::ok : status::corrupt;
    }
    case kind_list:
    case kind_set: {
        const type_ref &e = m[0].type;
        const std::uint32_t a = (std::max)(4u, e.alignment);
        const std::uint64_t stride = round_up(e.size, e.alignment);
        if (stride > UINT32_MAX)
            return status::corrupt;
        node.stride = static_cast<std::uint32_t>(stride);
        node.slots = static_cast<std::uint32_t>(round_up(4, a));
        out.alignment = a;
        return m[0].offset == node.slots && size == node.slots + std::uint64_t{node.capacity} * node.stride &&
                       size % a == 0
                   ? status::ok
                   : status::corrupt;
    }
    case kind_dict: {
        const type_ref &k = m[0].type;
        const type_ref &v = m[1].type;
        const std::uint32_t pair = (std::max)(k.alignment, v.alignment);
        const std::uint32_t a = (std::max)(4u, pair);
        if (m[0].offset != 0 || m[1].offset != round_up(k.size, v.alignment))
            return status::corrupt;
        const std::uint64_t stride = round_up(std::uint64_t{m[1].offset} + v.size, pair);
        if (stride > UINT32_MAX)
            return status::corrupt;
        node.stride = static_cast<std::uint32_t>(stride);
        node.slots = static_cast<std::uint32_t>(round_up(4, a));
        out.alignment = a;
        return size == node.slots + std::uint64_t{node.capacity} * node.stride && size % a == 0 ? status::ok
                                                                                                : status::corrupt;
    }
    default:
        return status::corrupt;
    }
}

inline status type_table::parse(std::span<const std::byte> table, std::span<const std::uint32_t> entries,
                                bool v1) noexcept {
    *this = type_table{};
    if (table.size() % 8 != 0 || table.size() > max_types_size || (v1 && !table.empty()) ||
        entries.size() > max_fields)
        return status::corrupt;
    const auto size = static_cast<std::uint32_t>(table.size());
    const auto count = static_cast<std::uint16_t>(entries.size());
    const std::size_t words = (std::size_t{size} / 8 + 63) / 64 + 1;
    bytes_.reset(new (std::nothrow) std::byte[size + 1]);
    entries_.reset(new (std::nothrow) std::uint32_t[count + 1u]);
    fields_.reset(new (std::nothrow) type_ref[count + 1u]);
    std::unique_ptr<std::uint64_t[]> seen(new (std::nothrow) std::uint64_t[words]());
    if (bytes_ == nullptr || entries_ == nullptr || fields_ == nullptr || seen == nullptr) {
        *this = type_table{};
        return status::os;
    }
    if (size > 0)
        std::memcpy(bytes_.get(), table.data(), size);
    std::copy(entries.begin(), entries.end(), entries_.get());
    size_ = size;
    field_count_ = count;
    v1_ = v1;
    const std::span<const std::byte> copy(bytes_.get(), size);
    type_parser counter(copy, seen.get(), nullptr);
    for (std::uint16_t i = 0; i < count; ++i) {
        type_ref ignored;
        if (const status rc = counter.field(entries[i], v1, ignored); rc != status::ok) {
            *this = type_table{};
            return rc;
        }
    }
    const type_counts &n = counter.counts;
    nodes_.reset(new (std::nothrow) type_node[n.nodes + 1u]());
    members_.reset(new (std::nothrow) type_member[n.members + 1u]());
    names_.reset(new (std::nothrow) std::string_view[n.names + 1u]());
    numbers_.reset(new (std::nothrow) std::uint64_t[n.numbers + 1u]());
    literals_.reset(new (std::nothrow) literal_value[n.literals + 1u]());
    if (nodes_ == nullptr || members_ == nullptr || names_ == nullptr || numbers_ == nullptr ||
        literals_ == nullptr) {
        *this = type_table{};
        return status::os;
    }
    node_count_ = n.nodes;
    std::fill_n(seen.get(), words, std::uint64_t{0});
    type_parser filler(copy, seen.get(), this);
    for (std::uint16_t i = 0; i < count; ++i)
        if (const status rc = filler.field(entries[i], v1, fields_[i]); rc != status::ok) {
            *this = type_table{};
            return rc;
        }
    return status::ok;
}

// Sets each field's kind and capacity from its type, then checks the fields lie inside the record,
// aligned, without sharing a byte. create refuses a kind this header does not know; open keeps it opaque.
inline status place_fields(field_spec *fields, std::uint16_t count, const type_table &types,
                           std::uint32_t record_size, bool unknown_ok) noexcept {
    for (std::uint16_t i = 0; i < count; ++i) {
        const type_ref &t = types.field(i);
        field_spec &f = fields[i];
        if (!unknown_ok && !kind_known(t.kind))
            return status::corrupt;
        f.kind = t.kind;
        f.capacity = prefixed(t.kind) ? t.size - 4 : t.size;
        if (f.offset % t.alignment != 0 || f.offset > record_size || t.size > record_size - f.offset)
            return status::corrupt;
    }
    return fields_overlap({fields, count}) ? status::corrupt : status::ok;
}

inline bool name_ok(std::string_view name) noexcept {
    if (name.empty() || name.size() > name_max)
        return false;
    for (const char c : name)
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.' ||
              c == '-'))
            return false;
    return true;
}

using object_name = std::array<char, object_name_max>;

// name must have passed name_ok.
inline object_name make_name(const char *prefix, std::string_view name, const char *suffix) noexcept {
    object_name out{};
    std::snprintf(out.data(), out.size(), "%ssharedbox.%.*s%s", prefix, static_cast<int>(name.size()), name.data(),
                  suffix);
    return out;
}

#ifdef _WIN32
using wide_name = std::array<WCHAR, object_name_max>;

inline wide_name make_wide_name(std::string_view name, const char *suffix) noexcept {
    const object_name narrow = make_name("Local\\", name, suffix);
    wide_name out{};
    // Box names are ASCII, so widening byte by byte is exact.
    for (std::size_t i = 0; narrow[i] != '\0'; ++i)
        out[i] = static_cast<WCHAR>(static_cast<unsigned char>(narrow[i]));
    return out;
}
#endif

// The OS error of the call that just failed, read before any cleanup can change it.
inline unexpected os_failure() noexcept {
#ifdef _WIN32
    return unexpected(error{status::os, static_cast<std::int32_t>(GetLastError()), 0});
#else
    return unexpected(error{status::os, errno, 0});
#endif
}

// One view of a named mapping and the OS handle it came from; the destructor unmaps and closes both.
class os_mapping {
public:
    os_mapping() noexcept = default;
    os_mapping(os_mapping &&other) noexcept { swap(other); }
    os_mapping &operator=(os_mapping &&other) noexcept {
        os_mapping(std::move(other)).swap(*this);
        return *this;
    }
    ~os_mapping() { reset(); }

    void reset() noexcept {
        if (base_ == nullptr)
            return;
#ifdef _WIN32
        UnmapViewOfFile(base_);
        CloseHandle(os_);
        os_ = nullptr;
#else
        munmap(base_, static_cast<std::size_t>(size_));
        close(os_);
        os_ = -1;
#endif
        base_ = nullptr;
        size_ = 0;
    }

    void *base() const noexcept { return base_; }
    std::uint64_t size() const noexcept { return size_; }

#ifdef _WIN32
    using native = HANDLE;
#else
    using native = int;
#endif
    native os() const noexcept { return os_; }

    // Takes ownership of an OS handle and its view.
    static os_mapping adopt(native os, void *base, std::uint64_t size) noexcept {
        os_mapping m;
        m.os_ = os;
        m.base_ = base;
        m.size_ = size;
        return m;
    }

private:
    void swap(os_mapping &other) noexcept {
        std::swap(os_, other.os_);
        std::swap(base_, other.base_);
        std::swap(size_, other.size_);
    }

#ifdef _WIN32
    HANDLE os_ = nullptr;
#else
    int os_ = -1;
#endif
    void *base_ = nullptr;
    std::uint64_t size_ = 0;
};

#ifndef _WIN32
// Reserves the pages now, so a /dev/shm too small for the box fails here with ENOSPC instead of
// killing a later writer with SIGBUS. ftruncate is the fallback where the file system cannot.
inline int allocate(int fd, std::uint64_t size) noexcept {
    int rc;
    // tmpfs stops a large allocation with EINTR whenever a signal is pending.
    do
        rc = posix_fallocate(fd, 0, static_cast<off_t>(size));
    while (rc == EINTR);
    if (rc == EOPNOTSUPP || rc == ENOSYS || rc == EINVAL)
        return ftruncate(fd, static_cast<off_t>(size));
    if (rc != 0)
        errno = rc;
    return rc == 0 ? 0 : -1;
}
#endif

inline result<os_mapping> map_create(std::string_view name, std::uint64_t size) noexcept {
#ifdef _WIN32
    const wide_name wide = make_wide_name(name, "");
    HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
                                        static_cast<DWORD>(size >> 32), static_cast<DWORD>(size), wide.data());
    // ERROR_INVALID_HANDLE: another kind of object already has the name.
    if (mapping == nullptr)
        return GetLastError() == ERROR_INVALID_HANDLE ? unexpected(status::exists) : os_failure();
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mapping);
        return unexpected(status::exists);
    }
    void *view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    if (view == nullptr) {
        const unexpected failed = os_failure();
        CloseHandle(mapping);
        return failed;
    }
    return os_mapping::adopt(mapping, view, size);
#else
    const object_name path = make_name("/", name, "");
    const int fd = shm_open(path.data(), O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0)
        return errno == EEXIST ? unexpected(status::exists) : os_failure();
    if (allocate(fd, size) == 0) {
        void *view = mmap(nullptr, static_cast<std::size_t>(size), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (view != MAP_FAILED)
            return os_mapping::adopt(fd, view, size);
    }
    const unexpected failed = os_failure();
    close(fd);
    shm_unlink(path.data());
    return failed;
#endif
}

// Maps the whole of an existing mapping. On Linux a creator sizes the name right after making it, so a
// mapping smaller than one page is waited for until wait expires.
inline result<os_mapping> map_open(std::string_view name, backoff &wait) noexcept {
#ifdef _WIN32
    static_cast<void>(wait);
    const wide_name wide = make_wide_name(name, "");
    HANDLE mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, wide.data());
    if (mapping == nullptr) {
        const DWORD e = GetLastError();
        return e == ERROR_FILE_NOT_FOUND || e == ERROR_INVALID_HANDLE ? unexpected(status::not_found)
                                                                      : os_failure();
    }
    void *view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    MEMORY_BASIC_INFORMATION info;
    if (view == nullptr || VirtualQuery(view, &info, sizeof info) == 0) {
        const unexpected failed = os_failure();
        if (view != nullptr)
            UnmapViewOfFile(view);
        CloseHandle(mapping);
        return failed;
    }
    return os_mapping::adopt(mapping, view, static_cast<std::uint64_t>(info.RegionSize));
#else
    const object_name path = make_name("/", name, "");
    const int fd = shm_open(path.data(), O_RDWR, 0);
    if (fd < 0)
        return errno == ENOENT ? unexpected(status::not_found) : os_failure();
    struct stat st;
    for (;;) {
        if (fstat(fd, &st) != 0) {
            const unexpected failed = os_failure();
            close(fd);
            return failed;
        }
        if (static_cast<std::uint64_t>(st.st_size) >= page_size)
            break;
        if (wait.expired()) {
            close(fd);
            return unexpected(status::not_found);
        }
        wait.pause();
    }
    void *view = mmap(nullptr, static_cast<std::size_t>(st.st_size), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (view == MAP_FAILED) {
        const unexpected failed = os_failure();
        close(fd);
        return failed;
    }
    return os_mapping::adopt(fd, view, static_cast<std::uint64_t>(st.st_size));
#endif
}

#ifndef _WIN32
// Fills buf from getrandom, or from /dev/urandom where the kernel is older than 3.17 or a seccomp
// profile refuses getrandom. Returns 0, or the errno of the failure (EIO for a read that returned 0).
inline int fill_random(unsigned char *buf, std::size_t size) noexcept {
    std::size_t got = 0;
    int err = 0;
    int fd = -1;
    while (got < size) {
        const ssize_t n = fd < 0 ? getrandom(buf + got, size - got, 0) : read(fd, buf + got, size - got);
        if (n > 0) {
            got += static_cast<std::size_t>(n);
            continue;
        }
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && fd < 0 && (errno == ENOSYS || errno == EPERM)) {
            do
                fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
            while (fd < 0 && errno == EINTR);
            if (fd >= 0)
                continue;
        }
        err = n < 0 ? errno : EIO;
        break;
    }
    if (fd >= 0)
        close(fd);
    return err;
}
#endif

// 8 bytes from the OS random source, never 0.
inline result<std::uint64_t> random_id() noexcept {
    std::uint64_t id = 0;
    while (id == 0) {
#ifdef _WIN32
        const NTSTATUS rc =
            BCryptGenRandom(nullptr, reinterpret_cast<PUCHAR>(&id), sizeof id, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
        // BCryptGenRandom does not set GetLastError, so there is no OS code to keep.
        if (!BCRYPT_SUCCESS(rc))
            return unexpected(status::os);
#else
        if (const int err = fill_random(reinterpret_cast<unsigned char *>(&id), sizeof id); err != 0)
            return unexpected(error{status::os, err, 0});
#endif
    }
    return id;
}

// Checks line 0 of a header, copied out of a mapping of mapped_size bytes, before anything reads
// through it. The caller has already refused a major version outside oldest_layout_major..layout_major.
inline status check_geometry(const header &h, std::uint64_t mapped_size) noexcept {
    if (h.field_count == 0 || h.field_count > max_fields)
        return status::corrupt;
    if (h.waiter_slots == 0 || h.waiter_slots > max_waiter_slots)
        return status::corrupt;
    if (h.tail != header_size || h.size != mapped_size || h.record % record_alignment != 0)
        return status::corrupt;
    const std::uint64_t slots_end = tail_end(h.field_count, h.waiter_slots);
    const std::uint64_t record_end = std::uint64_t{h.record} + h.record_size;
    if (h.layout_major == 1) {
        if (h.types_size != 0 || h.record < slots_end || h.record_size > max_record_size)
            return status::corrupt;
    } else {
        if (h.types_size % 8 != 0 || h.types_size > max_types_size ||
            h.record < round_up(slots_end + h.types_size, record_alignment) || record_end > max_mapping_size)
            return status::corrupt;
    }
    return record_end > mapped_size ? status::corrupt : status::ok;
}

inline bool major_ok(std::uint16_t major) noexcept {
    return major >= oldest_layout_major && major <= layout_major;
}

// Line 0 of the header at base, copied once so that only the copy is checked and used.
inline header copy_line0(const void *base) noexcept {
    header line0{};
    std::memcpy(&line0, base, 64);
    return line0;
}

struct state;

} // namespace detail

class handle;

namespace detail {

[[nodiscard]] inline result<handle> create_impl(std::string_view name, std::span<const field_spec> fields,
                                                std::uint32_t record_size, std::uint64_t schema_hash,
                                                std::uint16_t waiter_slots, std::span<const value> initial,
                                                std::span<const std::byte> types, std::uint16_t major,
                                                bool publish);

} // namespace detail

// The type of a field or of a member inside one, read from a handle's checked copy of the description
// table. Valid while that handle lives.
class type_view {
public:
    type_view() noexcept = default;

    std::uint8_t kind() const noexcept { return ref_.kind; }
    // Bytes the value takes in the record, a length prefix included.
    std::uint32_t size() const noexcept { return ref_.size; }
    std::uint32_t alignment() const noexcept { return ref_.alignment; }
    bool described() const noexcept { return ref_.node != detail::no_node; }
    // enum, flag, literal: values; optional 1; union, record, tuple: members; list and set 1; dict 2; else 0.
    std::uint16_t count() const noexcept { return described() ? node().count : 0; }
    // str, bytes, decimal: bytes; list, set, dict: elements; else 0.
    std::uint32_t capacity() const noexcept {
        if (detail::prefixed(ref_.kind))
            return ref_.size - 4;
        return described() ? node().capacity : 0;
    }
    // list, set, dict: where the first slot starts and how many bytes each takes.
    std::uint32_t slots() const noexcept { return described() ? node().slots : 0; }
    std::uint32_t stride() const noexcept { return described() ? node().stride : 0; }
    // optional, union, record, tuple: i < count(); list and set: 0, the element; dict: 0 key, 1 value.
    // Another kind or index gives an empty type_view and offset 0.
    type_view member(std::uint16_t i) const noexcept {
        return has(i, kind_optional, kind_dict) ? type_view{table_, table_->member(node().members + i).type}
                                                : type_view{};
    }
    std::uint32_t member_offset(std::uint16_t i) const noexcept {
        return has(i, kind_optional, kind_dict) ? table_->member(node().members + i).offset : 0;
    }
    // enum, flag, record; another kind or index gives an empty name.
    std::string_view name(std::uint16_t i) const noexcept {
        const bool named = has(i, kind_enum, kind_flag) || has(i, kind_record, kind_record);
        return named ? table_->name(node().names + i) : std::string_view{};
    }
    // flag; another kind or index gives 0.
    std::uint64_t flag_bits(std::uint16_t i) const noexcept {
        return has(i, kind_flag, kind_flag) ? table_->number(node().numbers + i) : 0;
    }
    // literal; another kind or index gives a None value; compare i with count() to tell them apart.
    const literal_value &literal(std::uint16_t i) const noexcept {
        static constexpr literal_value empty{};
        return has(i, kind_literal, kind_literal) ? table_->literal(node().literals + i) : empty;
    }
    dl_dtype dtype() const noexcept { return described() ? node().dtype : dl_dtype{}; }
    std::span<const std::uint64_t> shape() const noexcept {
        if (!described() || ref_.kind != kind_array)
            return {};
        return {&table_->numbers_view(node().numbers), node().ndim};
    }
    // The description's bytes, as the segment stores them; empty for a kind without one.
    std::span<const std::byte> description() const noexcept {
        return described() ? table_->bytes().subspan(node().at, node().length) : std::span<const std::byte>{};
    }

private:
    friend class handle;
    type_view(const detail::type_table *table, detail::type_ref ref) noexcept : table_(table), ref_(ref) {}
    const detail::type_node &node() const noexcept { return table_->node(ref_.node); }
    // Whether the kind is in [first, last] and i is below count().
    bool has(std::uint16_t i, std::uint8_t first, std::uint8_t last) const noexcept {
        return described() && ref_.kind >= first && ref_.kind <= last && i < node().count;
    }

    const detail::type_table *table_ = nullptr;
    detail::type_ref ref_{};
};

inline constexpr std::int32_t max_date_ordinal = 3652059;
// date(1970, 1, 1).toordinal(): the day sys_days counts from.
inline constexpr std::int32_t unix_epoch_ordinal = 719163;
inline constexpr std::int64_t micros_per_day = 86400000000;
inline constexpr std::int16_t max_offset_minutes = 1439;

// A time of day: wall time, the offset from UTC in minutes (0 when naive), and Python's fold.
struct time_value {
    std::chrono::microseconds of_day;
    std::int16_t offset;
    bool naive;
    bool fold;
};

// A date and time: micros since 1970-01-01T00:00, wall time when naive and UTC when aware.
struct datetime_value {
    std::int64_t micros;
    std::int16_t offset;
    bool naive;
    bool fold;

    // The wall time, naive or not.
    std::chrono::local_time<std::chrono::microseconds> local() const noexcept {
        return std::chrono::local_time<std::chrono::microseconds>(
            std::chrono::microseconds(micros + (naive ? 0 : std::int64_t{offset} * 60000000)));
    }
    // The instant; for a naive value, the wall time read as UTC.
    std::chrono::sys_time<std::chrono::microseconds> utc() const noexcept {
        return std::chrono::sys_time<std::chrono::microseconds>(std::chrono::microseconds(micros));
    }
};

// Python's timedelta, normalised as Python stores it.
struct timedelta_value {
    std::int32_t days;
    std::int32_t seconds;
    std::int32_t microseconds;
};

namespace detail {

template <class T> T load(std::span<const std::byte> bytes, std::size_t at) noexcept {
    T value;
    std::memcpy(&value, bytes.data() + at, sizeof value);
    return value;
}

template <class T> void put(std::span<std::byte> bytes, std::size_t at, T value) noexcept {
    std::memcpy(bytes.data() + at, &value, sizeof value);
}

inline constexpr std::int64_t min_wall = std::int64_t{1 - unix_epoch_ordinal} * micros_per_day;
inline constexpr std::int64_t max_wall =
    std::int64_t{max_date_ordinal - unix_epoch_ordinal + 1} * micros_per_day - 1;
inline constexpr std::uint8_t flag_naive = 1;
inline constexpr std::uint8_t flag_fold = 2;

inline bool offset_ok(std::int16_t offset, bool naive) noexcept {
    return offset >= -max_offset_minutes && offset <= max_offset_minutes && (!naive || offset == 0);
}

// Wall time of a datetime value, or nullopt-like false when its fields could not come from Python.
inline bool wall_ok(std::int64_t micros, std::int16_t offset, bool naive) noexcept {
    // Checked before the sum, which a forged micros near the int64 limits would overflow.
    if (micros < min_wall - micros_per_day || micros > max_wall + micros_per_day)
        return false;
    const std::int64_t wall = micros + (naive ? 0 : std::int64_t{offset} * 60000000);
    return wall >= min_wall && wall <= max_wall;
}

} // namespace detail

[[nodiscard]] inline result<std::complex<double>> decode_complex(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 16)
        return unexpected(status::range);
    return std::complex<double>(detail::load<double>(bytes, 0), detail::load<double>(bytes, 8));
}

[[nodiscard]] inline status encode_complex(std::complex<double> value, std::span<std::byte> out) noexcept {
    if (out.size() != 16)
        return status::range;
    detail::put(out, 0, value.real());
    detail::put(out, 8, value.imag());
    return status::ok;
}

[[nodiscard]] inline result<std::chrono::sys_days> decode_date(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 4)
        return unexpected(status::range);
    const auto ordinal = detail::load<std::int32_t>(bytes, 0);
    if (ordinal < 1 || ordinal > max_date_ordinal)
        return unexpected(status::corrupt);
    return std::chrono::sys_days(std::chrono::days(ordinal - unix_epoch_ordinal));
}

[[nodiscard]] inline status encode_date(std::chrono::sys_days day, std::span<std::byte> out) noexcept {
    const std::int64_t ordinal = std::int64_t{day.time_since_epoch().count()} + unix_epoch_ordinal;
    if (out.size() != 4 || ordinal < 1 || ordinal > max_date_ordinal)
        return status::range;
    detail::put(out, 0, static_cast<std::int32_t>(ordinal));
    return status::ok;
}

[[nodiscard]] inline result<time_value> decode_time(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 16)
        return unexpected(status::range);
    const auto micros = detail::load<std::int64_t>(bytes, 0);
    const auto offset = detail::load<std::int16_t>(bytes, 8);
    const auto flags = detail::load<std::uint8_t>(bytes, 10);
    const bool naive = (flags & detail::flag_naive) != 0;
    if ((flags & ~3u) != 0 || micros < 0 || micros >= micros_per_day || !detail::offset_ok(offset, naive))
        return unexpected(status::corrupt);
    return time_value{std::chrono::microseconds(micros), offset, naive, (flags & detail::flag_fold) != 0};
}

[[nodiscard]] inline status encode_time(const time_value &value, std::span<std::byte> out) noexcept {
    const std::int64_t micros = value.of_day.count();
    if (out.size() != 16 || micros < 0 || micros >= micros_per_day ||
        !detail::offset_ok(value.offset, value.naive))
        return status::range;
    std::memset(out.data(), 0, out.size());
    detail::put(out, 0, micros);
    detail::put(out, 8, value.offset);
    detail::put(
        out, 10,
        static_cast<std::uint8_t>((value.naive ? detail::flag_naive : 0) | (value.fold ? detail::flag_fold : 0)));
    return status::ok;
}

[[nodiscard]] inline result<datetime_value> decode_datetime(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 16)
        return unexpected(status::range);
    const auto micros = detail::load<std::int64_t>(bytes, 0);
    const auto offset = detail::load<std::int16_t>(bytes, 8);
    const auto flags = detail::load<std::uint8_t>(bytes, 10);
    const bool naive = (flags & detail::flag_naive) != 0;
    if ((flags & ~3u) != 0 || !detail::offset_ok(offset, naive) || !detail::wall_ok(micros, offset, naive))
        return unexpected(status::corrupt);
    return datetime_value{micros, offset, naive, (flags & detail::flag_fold) != 0};
}

[[nodiscard]] inline status encode_datetime(const datetime_value &value, std::span<std::byte> out) noexcept {
    if (out.size() != 16 || !detail::offset_ok(value.offset, value.naive) ||
        !detail::wall_ok(value.micros, value.offset, value.naive))
        return status::range;
    std::memset(out.data(), 0, out.size());
    detail::put(out, 0, value.micros);
    detail::put(out, 8, value.offset);
    detail::put(
        out, 10,
        static_cast<std::uint8_t>((value.naive ? detail::flag_naive : 0) | (value.fold ? detail::flag_fold : 0)));
    return status::ok;
}

[[nodiscard]] inline result<timedelta_value> decode_timedelta(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 12)
        return unexpected(status::range);
    const timedelta_value value{detail::load<std::int32_t>(bytes, 0), detail::load<std::int32_t>(bytes, 4),
                                detail::load<std::int32_t>(bytes, 8)};
    if (value.days < -999999999 || value.days > 999999999 || value.seconds < 0 || value.seconds > 86399 ||
        value.microseconds < 0 || value.microseconds > 999999)
        return unexpected(status::corrupt);
    return value;
}

[[nodiscard]] inline status encode_timedelta(const timedelta_value &value, std::span<std::byte> out) noexcept {
    if (out.size() != 12 || value.days < -999999999 || value.days > 999999999 || value.seconds < 0 ||
        value.seconds > 86399 || value.microseconds < 0 || value.microseconds > 999999)
        return status::range;
    detail::put(out, 0, value.days);
    detail::put(out, 4, value.seconds);
    detail::put(out, 8, value.microseconds);
    return status::ok;
}

[[nodiscard]] inline result<std::array<std::byte, 16>> decode_uuid(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 16)
        return unexpected(status::range);
    std::array<std::byte, 16> out;
    std::memcpy(out.data(), bytes.data(), 16);
    return out;
}

[[nodiscard]] inline status encode_uuid(const std::array<std::byte, 16> &value,
                                        std::span<std::byte> out) noexcept {
    if (out.size() != 16)
        return status::range;
    std::memcpy(out.data(), value.data(), 16);
    return status::ok;
}

[[nodiscard]] inline result<bool> decode_bool(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 1)
        return unexpected(status::range);
    const auto byte = detail::load<std::uint8_t>(bytes, 0);
    if (byte > 1)
        return unexpected(status::corrupt);
    return byte == 1;
}

// An optional's presence byte, the first byte of its value.
[[nodiscard]] inline result<bool> decode_present(std::span<const std::byte> bytes) noexcept {
    return bytes.empty() ? result<bool>(unexpected(status::range)) : decode_bool(bytes.first(1));
}

[[nodiscard]] inline result<std::uint64_t> decode_flag(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 8)
        return unexpected(status::range);
    return detail::load<std::uint64_t>(bytes, 0);
}

[[nodiscard]] inline status encode_flag(std::uint64_t bits, std::span<std::byte> out) noexcept {
    if (out.size() != 8)
        return status::range;
    detail::put(out, 0, bits);
    return status::ok;
}

// The member position of an enum or the value position of a literal.
[[nodiscard]] inline result<std::uint16_t> decode_position(type_view type,
                                                           std::span<const std::byte> bytes) noexcept {
    if ((type.kind() != kind_enum && type.kind() != kind_literal) || bytes.size() != 2)
        return unexpected(status::range);
    const auto position = detail::load<std::uint16_t>(bytes, 0);
    if (position >= type.count())
        return unexpected(status::corrupt);
    return position;
}

[[nodiscard]] inline status encode_position(type_view type, std::uint16_t position,
                                            std::span<std::byte> out) noexcept {
    if ((type.kind() != kind_enum && type.kind() != kind_literal) || out.size() != 2 || position >= type.count())
        return status::range;
    detail::put(out, 0, position);
    return status::ok;
}

// A union's tag, the first byte of its value: the member it holds.
[[nodiscard]] inline result<std::uint8_t> decode_tag(type_view type, std::span<const std::byte> bytes) noexcept {
    if (type.kind() != kind_union || bytes.empty())
        return unexpected(status::range);
    const auto tag = detail::load<std::uint8_t>(bytes, 0);
    if (tag >= type.count())
        return unexpected(status::corrupt);
    return tag;
}

// The length of a list, set or dict value, the u32 its value starts with.
[[nodiscard]] inline result<std::uint32_t> decode_length(type_view type,
                                                         std::span<const std::byte> bytes) noexcept {
    const std::uint8_t kind = type.kind();
    if ((kind != kind_list && kind != kind_set && kind != kind_dict) || bytes.size() < 4)
        return unexpected(status::range);
    const auto length = detail::load<std::uint32_t>(bytes, 0);
    if (length > type.capacity() || bytes.size() < type.slots() + std::uint64_t{length} * type.stride())
        return unexpected(status::corrupt);
    return length;
}

// A box's segment, mapped into this process. Move-only; the destructor releases it. Every member
// function may be called from several threads at once; destroying or moving a handle must not overlap
// another call on it.
class handle {
public:
    handle() noexcept = default;
    handle(handle &&other) noexcept : s_(std::exchange(other.s_, nullptr)) {}
    handle &operator=(handle &&other) noexcept {
        handle(std::move(other)).swap(*this);
        return *this;
    }
    ~handle();

    // Creates the box: the name, the mapping, the header and field table, every waiter slot free, and
    // the initial values; no other process can open it before all of that is written. types is the
    // description table of the fields of a described kind; empty when there are none.
    [[nodiscard]] static result<handle> create(std::string_view name, std::span<const field_spec> fields,
                                               std::uint32_t record_size, std::uint64_t schema_hash,
                                               std::uint16_t waiter_slots, std::span<const value> initial,
                                               std::span<const std::byte> types = {});
    // As create, but open waits until publish() is called, so the creator can change the values first.
    [[nodiscard]] static result<handle>
    create_unpublished(std::string_view name, std::span<const field_spec> fields, std::uint32_t record_size,
                       std::uint64_t schema_hash, std::uint16_t waiter_slots, std::span<const value> initial,
                       std::span<const std::byte> types = {});
    // Lets open find a box made by create_unpublished. status::range if the box is already published,
    // as every box a handle made by open or from_capsule reaches is. A handle from duplicate() of an
    // unpublished creator shares its mapping and could publish it, so a creator must not hand one out
    // before publishing.
    [[nodiscard]] result<void> publish() noexcept;
    // Opens the box called name, waiting up to timeout for a creator that has not finished. The caller
    // compares schema_hash() with its own.
    [[nodiscard]] static result<handle> open(std::string_view name, seconds timeout);

    std::string_view name() const noexcept;
    std::uint16_t field_count() const noexcept;
    // The field as checked when the handle was made; later changes to the shared table are ignored.
    // index must be below field_count().
    const field_spec &field(std::uint16_t index) const noexcept;
    // The type of a field: kind, size and, for a described kind, its description. Valid while this handle
    // lives; index must be below field_count().
    type_view field_type(std::uint16_t index) const noexcept;
    // The description table as checked when the handle was made; empty for a 1.0 segment.
    std::span<const std::byte> types_table() const noexcept;
    std::uint32_t record_size() const noexcept;
    std::uint64_t schema_hash() const noexcept;
    // Random at creation, so a box made again under the same name has another.
    std::uint64_t create_id() const noexcept;
    std::uint16_t waiter_slots() const noexcept;
    // The segment's minor version, or this header's if that is lower.
    std::uint16_t minor_version() const noexcept;
    // The segment's major version: 1 for a box made by a 0.3 release, 2 otherwise.
    std::uint16_t major_version() const noexcept;
    void *base() const noexcept;
    std::uint64_t size() const noexcept;

    // How long a read waits for another writer's lock before it gives status::lock_timeout; 5 s until set.
    [[nodiscard]] result<void> set_lock_timeout(seconds timeout) noexcept;
    // Called around a wait for another writer's lock: before the first pause and after the last.
    void set_wait_hooks(void *(*before)(), void (*after)(void *)) noexcept;
    // Copies the field's stored bytes, and no more, into buf, with the field's write count from the same
    // moment. If they do not fit, nothing is copied and len says how many bytes they need; a buf of the
    // field's capacity always fits.
    [[nodiscard]] result<read_value> read(std::uint16_t field, std::span<std::byte> buf) const;
    // Copies the whole record, every field from one moment, into buf of at least record_size() bytes,
    // and returns the generation of that moment.
    [[nodiscard]] result<std::uint64_t> read_record(std::span<std::byte> buf) const;
    // As read, for a list, set or dict field: copies the length and the slots the value uses, not the
    // whole capacity. status::range for a field of another kind.
    [[nodiscard]] result<read_value> read_used(std::uint16_t field, std::span<std::byte> buf) const;
    // As read and read_record, for values of large_copy bytes or more: the wait hooks run once around
    // the whole call, and a copy starts again as soon as a writer moves the sequence number.
    [[nodiscard]] result<read_value> read_large(std::uint16_t field, std::span<std::byte> buf) const;
    [[nodiscard]] result<std::uint64_t> read_record_large(std::span<std::byte> buf) const;
    // As write, with the wait hooks run once around the whole call.
    [[nodiscard]] result<void> write_large(std::span<const value> values, seconds lock_timeout);
    // The stored bytes of field inside a copy made by read_record.
    std::span<const std::byte> payload(std::uint16_t field, std::span<const std::byte> record) const noexcept;
    // Writes every value under one lock, so readers see all of them or none, then wakes waiters.
    // status::range for a field of a kind this header does not know.
    [[nodiscard]] result<void> write(std::span<const value> values, seconds lock_timeout);
    // Writes since creation, seq >> 1: every write adds 2 to seq, and a force_unlock counts as one.
    std::uint64_t generation() const noexcept;
    // Writes to the field since creation; field must be below field_count().
    std::uint64_t version(std::uint16_t field) const noexcept;
    // The pid holding the write lock, 0 when free; after force_unlock, the last holder.
    std::uint32_t writer_pid() const noexcept;
    // Releases a write lock left by a process that died while writing. writer_pid keeps its value.
    [[nodiscard]] result<void> force_unlock() noexcept;
    // Takes the write lock and returns the even seq it was taken from, for unlock; write does both
    // itself. For tests that hold the lock.
    [[nodiscard]] result<std::uint64_t> lock(seconds lock_timeout);
    void unlock(std::uint64_t locked) noexcept;
    // Takes the write lock and returns the bytes of an array field for writing in place, with the seq to end
    // with. status::range for a field past field_count() or of another kind. Readers in every process retry
    // until end_write.
    [[nodiscard]] result<field_write> begin_write(std::uint16_t field, seconds lock_timeout);
    // Adds one to the field's write count, releases the lock begin_write took and wakes waiters.
    void end_write(const field_write &w) noexcept;

    // Claims a free waiter slot for this process, first freeing the slots of processes that have exited.
    [[nodiscard]] result<std::uint16_t> register_waiter();
    // Frees a slot this handle claimed; any other slot is left alone.
    void release_waiter(std::uint16_t slot) noexcept;
    // Whether this handle claimed slot and the slot still records this process; false once it was freed
    // under it, when the caller releases it and claims another.
    bool waiter_held(std::uint16_t slot) const noexcept;
    // Occupied waiter slots.
    std::uint32_t waiters() const noexcept;
    // Blocks in a slot this handle holds until the generation differs from last_generation, the slot is
    // interrupted, or timeout passes (status::timeout).
    [[nodiscard]] result<wake> wait(std::uint16_t slot, std::uint64_t last_generation, seconds timeout);
    // Ends the wait in slot, of any process, with wake::interrupted; the flag stays set until that waiter
    // sees it, so an interrupt sent before the wait starts still ends it. An interrupt that arrives after the
    // slot was released and claimed again reaches the next owner as one spurious wake::interrupted.
    [[nodiscard]] result<void> interrupt(std::uint16_t slot);

    // Takes over a handle made by another build, such as the one in a "sharedbox_box" capsule: checks
    // the segment at capsule->base as open does and, on success, clears capsule->release; destroying
    // the new handle calls the producer's release.
    [[nodiscard]] static result<handle> from_capsule(sbx_handle *capsule);
    // A second handle on this segment with its own mapping, made from this handle's OS handle rather
    // than the name, so it works after unlink. status::range for a handle made by from_capsule.
    [[nodiscard]] result<handle> duplicate() const;
    // A heap sbx_handle that owns this handle; its release destroys the handle, and the caller deletes
    // the struct afterwards. nullptr when memory runs out, with this handle left as it was.
    [[nodiscard]] sbx_handle *to_capsule() &&;

private:
    template <bool Hooks> result<std::uint64_t> lock_with(seconds lock_timeout);
    explicit handle(detail::state *s) noexcept : s_(s) {}
    void swap(handle &other) noexcept { std::swap(s_, other.s_); }

    detail::state *s_ = nullptr;

    friend result<handle> detail::create_impl(std::string_view name, std::span<const field_spec> fields,
                                              std::uint32_t record_size, std::uint64_t schema_hash,
                                              std::uint16_t waiter_slots, std::span<const value> initial,
                                              std::span<const std::byte> types, std::uint16_t major, bool publish);
};

namespace detail {

// What a handle keeps: the checked copy of line 0 and the field table, pointers into the mapping, and
// the per-process bookkeeping.
struct state {
    char name[name_max + 1] = {};
    std::uint16_t field_count = 0;
    std::uint16_t waiter_slots = 0;
    std::uint16_t layout_major = 0;
    std::uint16_t layout_minor = 0;
    std::uint32_t record_size = 0;
    std::uint32_t record_offset = 0;
    std::uint64_t schema_hash = 0;
    std::uint64_t create_id = 0;
    header *hdr = nullptr;
    std::uint64_t *counts = nullptr;
    waiter_slot *slots = nullptr;
    std::byte *record = nullptr;
    std::uint64_t size = 0;
    std::unique_ptr<field_spec[]> fields;
    type_table types;
    os_mapping map;
    double lock_timeout = default_lock_timeout;
    void *(*before_wait)() = nullptr;
    void (*after_wait)(void *) = nullptr;
    std::array<std::atomic<std::uint64_t>, max_waiter_slots / 64> owned{};
#ifdef _WIN32
    std::unique_ptr<std::atomic<HANDLE>[]> events;
#endif
    sbx_handle foreign{};

    ~state();
};

inline bool owned_clear(state &s, std::uint16_t i) noexcept;
inline bool slot_is_mine(const state &s, std::uint16_t i) noexcept;
inline void free_slot(const state &s, std::uint16_t i) noexcept;
inline void free_dead_waiters(const state &s) noexcept;
SHAREDBOX_HOT void wake_waiters(const state &s) noexcept;
#ifdef _WIN32
inline HANDLE event(const state &s, std::uint16_t slot) noexcept;
#endif

inline state::~state() {
    if (hdr != nullptr)
        for (std::uint16_t i = 0; i < waiter_slots; ++i)
            // A child created by fork inherits the bits of its parent's slots; those stay the parent's.
            if (owned_clear(*this, i) && slot_is_mine(*this, i))
                free_slot(*this, i);
#ifdef _WIN32
    if (events != nullptr)
        for (std::uint16_t i = 0; i < waiter_slots; ++i)
            if (HANDLE e = events[i].load(std::memory_order_relaxed); e != nullptr)
                CloseHandle(e);
#endif
    if (foreign.release != nullptr)
        foreign.release(&foreign);
}

// A state for a mapping of the given shape, or nullptr when memory runs out.
inline std::unique_ptr<state> make_state(std::string_view name, std::uint16_t field_count,
                                         std::uint16_t waiter_slots) noexcept {
    std::unique_ptr<state> s(new (std::nothrow) state);
    if (s == nullptr)
        return nullptr;
    s->fields.reset(new (std::nothrow) field_spec[field_count]);
    if (s->fields == nullptr)
        return nullptr;
#ifdef _WIN32
    s->events.reset(new (std::nothrow) std::atomic<HANDLE>[waiter_slots]());
    if (s->events == nullptr)
        return nullptr;
#endif
    std::memcpy(s->name, name.data(), name.size());
    s->field_count = field_count;
    s->waiter_slots = waiter_slots;
    return s;
}

// Points s into the mapping at base, whose line 0 and field table s already holds.
inline void bind(state &s, void *base, std::uint64_t size, std::uint16_t segment_major,
                 std::uint16_t segment_minor) noexcept {
    auto *bytes = static_cast<std::byte *>(base);
    const std::size_t table = header_size + std::size_t{s.field_count} * sizeof(stored_field);
    s.hdr = static_cast<header *>(base);
    s.counts = reinterpret_cast<std::uint64_t *>(bytes + table);
    s.slots = reinterpret_cast<waiter_slot *>(bytes + table + std::size_t{s.field_count} * sizeof(std::uint64_t));
    s.record = bytes + s.record_offset;
    s.size = size;
    s.layout_major = segment_major;
    s.layout_minor = segment_minor < sharedbox::layout_minor ? segment_minor : sharedbox::layout_minor;
}

// Copies and checks the field table and the description table of a mapping whose line 0 passed
// check_geometry.
inline status copy_fields(state &s, const void *base, const header &line0) noexcept {
    const auto *bytes = static_cast<const std::byte *>(base);
    std::unique_ptr<std::uint32_t[]> entries(new (std::nothrow) std::uint32_t[s.field_count]);
    if (entries == nullptr)
        return status::os;
    for (std::uint16_t i = 0; i < s.field_count; ++i) {
        stored_field stored;
        std::memcpy(&stored, bytes + header_size + std::size_t{i} * sizeof stored, sizeof stored);
        s.fields[i].offset = stored.offset;
        entries[i] = stored.capacity_and_kind;
    }
    const std::span<const std::byte> table(bytes + tail_end(s.field_count, s.waiter_slots), line0.types_size);
    if (const status rc = s.types.parse(table, {entries.get(), s.field_count}, line0.layout_major == 1);
        rc != status::ok)
        return rc;
    return place_fields(s.fields.get(), s.field_count, s.types, s.record_size, true);
}

inline bool is_collection(std::uint32_t kind) noexcept { return kind - kind_list <= 2u; }

// A list, set or dict value is its length and the slots it uses, so its bytes may be shorter than the field.
inline bool collection_fits(const type_node &node, std::span<const std::byte> bytes) noexcept {
    if (bytes.size() < node.slots)
        return false;
    std::uint32_t length = 0;
    std::memcpy(&length, bytes.data(), sizeof length);
    return length <= node.capacity && bytes.size() == node.slots + std::uint64_t{length} * node.stride;
}

SHAREDBOX_HOT bool values_ok(const state &s, std::span<const value> values) noexcept {
    for (const value &v : values) {
        if (v.field >= s.field_count)
            return false;
        const field_spec &f = s.fields[v.field];
        if (!kind_known(f.kind))
            return false;
        if (prefixed(f.kind)) {
            if (v.bytes.size() > f.capacity)
                return false;
        } else if (is_collection(f.kind)) [[unlikely]] {
            if (!collection_fits(s.types.node(s.types.field(v.field).node), v.bytes))
                return false;
        } else if (v.bytes.size() != f.capacity) {
            return false;
        }
    }
    return true;
}

SHAREDBOX_HOT void store(const state &s, const value &v) noexcept {
    const field_spec &f = s.fields[v.field];
    std::byte *dst = s.record + f.offset;
    if (prefixed(f.kind)) {
        const auto length = static_cast<std::uint32_t>(v.bytes.size());
        std::memcpy(dst, &length, sizeof length);
        dst += sizeof length;
    }
    if (!v.bytes.empty())
        std::memcpy(dst, v.bytes.data(), v.bytes.size());
}

// The field's payload in record; a torn read may see any length, so it is capped at the capacity.
SHAREDBOX_HOT std::span<const std::byte> payload(const field_spec &f, const std::byte *record) noexcept {
    const std::byte *src = record + f.offset;
    if (!prefixed(f.kind))
        return {src, f.capacity};
    std::uint32_t length;
    std::memcpy(&length, src, sizeof length);
    return {src + sizeof length, length > f.capacity ? f.capacity : length};
}

// create and create_unpublished; tests make 1.0 boxes with major 1, which takes no description table.
inline result<handle> create_impl(std::string_view name, std::span<const field_spec> fields,
                                  std::uint32_t record_size, std::uint64_t schema_hash, std::uint16_t waiter_slots,
                                  std::span<const value> initial, std::span<const std::byte> types,
                                  std::uint16_t major, bool publish) {
    if (!name_ok(name) || fields.empty() || fields.size() > max_fields || waiter_slots == 0 ||
        waiter_slots > max_waiter_slots || !major_ok(major) || (major == 1 && !types.empty()))
        return unexpected(status::range);
    if (major == 1 ? record_size > max_record_size : std::uint64_t{record_size} > max_mapping_size)
        return unexpected(status::range);
    const auto count = static_cast<std::uint16_t>(fields.size());
    std::unique_ptr<std::uint32_t[]> entries(new (std::nothrow) std::uint32_t[fields.size()]);
    if (entries == nullptr)
        return unexpected(status::os);
    for (std::size_t i = 0; i < fields.size(); ++i) {
        if (fields[i].capacity > capacity_mask)
            return unexpected(status::range);
        entries[i] = fields[i].capacity | std::uint32_t{fields[i].kind} << kind_shift;
    }
    std::unique_ptr<state> s = make_state(name, count, waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    std::copy(fields.begin(), fields.end(), s->fields.get());
    // A table the caller built wrong is its mistake, status::range; running out of memory is not.
    if (const status rc = s->types.parse(types, {entries.get(), count}, major == 1); rc != status::ok)
        return unexpected(rc == status::os ? status::os : status::range);
    if (place_fields(s->fields.get(), count, s->types, record_size, false) != status::ok ||
        !values_ok(*s, initial))
        return unexpected(status::range);
    const auto record =
        static_cast<std::uint32_t>(round_up(tail_end(count, waiter_slots) + types.size(), record_alignment));
    if (major == 2 && std::uint64_t{record} + record_size > max_mapping_size)
        return unexpected(status::range);
    const std::uint64_t size = round_up(std::uint64_t{record} + record_size, page_size);
    const result<std::uint64_t> id = random_id();
    if (!id)
        return unexpected(id.error());
    result<os_mapping> map = map_create(name, size);
    if (!map)
        return unexpected(map.error());
    s->map = std::move(*map);
    auto *base = s->map.base();
    header &h = *static_cast<header *>(base);
    h.layout_major = major;
    h.types_size = static_cast<std::uint32_t>(types.size());
    h.layout_minor = layout_minor;
    h.field_count = count;
    h.waiter_slots = waiter_slots;
    h.schema_hash = schema_hash;
    h.record_size = record_size;
    h.record = record;
    h.tail = header_size;
    h.size = static_cast<std::uint32_t>(size);
    h.create_id = *id;
    h.creator_start = current_start();
    h.creator_pid = current_pid();
    h.creator_pidns = current_pidns();
    for (std::uint16_t i = 0; i < count; ++i) {
        const stored_field stored{fields[i].offset, entries[i]};
        std::memcpy(static_cast<std::byte *>(base) + header_size + std::size_t{i} * sizeof stored, &stored,
                    sizeof stored);
    }
    if (!types.empty())
        std::memcpy(static_cast<std::byte *>(base) + tail_end(count, waiter_slots), types.data(), types.size());
    s->record_size = record_size;
    s->record_offset = record;
    s->schema_hash = schema_hash;
    s->create_id = *id;
    bind(*s, base, size, major, layout_minor);
    for (const value &v : initial)
        store(*s, v);
    handle made(s.release());
    if (publish)
        static_cast<void>(made.publish());
    return made;
}

} // namespace detail

inline handle::~handle() { delete s_; }

inline result<handle> handle::create(std::string_view name, std::span<const field_spec> fields,
                                     std::uint32_t record_size, std::uint64_t schema_hash,
                                     std::uint16_t waiter_slots, std::span<const value> initial,
                                     std::span<const std::byte> types) {
    return detail::create_impl(name, fields, record_size, schema_hash, waiter_slots, initial, types, layout_major,
                               true);
}

inline result<handle> handle::create_unpublished(std::string_view name, std::span<const field_spec> fields,
                                                 std::uint32_t record_size, std::uint64_t schema_hash,
                                                 std::uint16_t waiter_slots, std::span<const value> initial,
                                                 std::span<const std::byte> types) {
    return detail::create_impl(name, fields, record_size, schema_hash, waiter_slots, initial, types, layout_major,
                               false);
}

inline result<void> handle::publish() noexcept {
    std::uint64_t unpublished = 0;
    if (!detail::atomic(s_->hdr->magic)
             .compare_exchange_strong(unpublished, magic, std::memory_order_release, std::memory_order_relaxed))
        return unexpected(status::range);
    return {};
}

inline result<handle> handle::open(std::string_view name, seconds timeout) {
    if (!detail::name_ok(name) || !detail::timeout_ok(timeout, false))
        return unexpected(status::range);
    detail::backoff wait(timeout.count());
    result<detail::os_mapping> map = detail::map_open(name, wait);
    if (!map)
        return unexpected(map.error());
    auto *hdr = static_cast<header *>(map->base());
    while (detail::atomic(hdr->magic).load(std::memory_order_acquire) != magic) {
        if (wait.expired())
            return unexpected(status::not_found);
        wait.pause();
    }
    const header line0 = detail::copy_line0(hdr);
    if (!detail::major_ok(line0.layout_major))
        return unexpected(status::layout);
    if (const status rc = detail::check_geometry(line0, map->size()); rc != status::ok)
        return unexpected(rc);
    std::unique_ptr<detail::state> s = detail::make_state(name, line0.field_count, line0.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    s->record_size = line0.record_size;
    if (const status rc = detail::copy_fields(*s, hdr, line0); rc != status::ok)
        return unexpected(rc);
    s->record_offset = line0.record;
    s->schema_hash = line0.schema_hash;
    s->create_id = line0.create_id;
    s->map = std::move(*map);
    detail::bind(*s, s->map.base(), s->map.size(), line0.layout_major, line0.layout_minor);
    detail::free_dead_waiters(*s);
    return handle(s.release());
}

inline std::string_view handle::name() const noexcept { return s_->name; }
inline std::uint16_t handle::field_count() const noexcept { return s_->field_count; }
inline const field_spec &handle::field(std::uint16_t index) const noexcept { return s_->fields[index]; }
inline type_view handle::field_type(std::uint16_t index) const noexcept {
    return {&s_->types, s_->types.field(index)};
}
inline std::span<const std::byte> handle::types_table() const noexcept { return s_->types.bytes(); }
inline std::uint32_t handle::record_size() const noexcept { return s_->record_size; }
inline std::uint64_t handle::schema_hash() const noexcept { return s_->schema_hash; }
inline std::uint64_t handle::create_id() const noexcept { return s_->create_id; }
inline std::uint16_t handle::waiter_slots() const noexcept { return s_->waiter_slots; }
inline std::uint16_t handle::minor_version() const noexcept { return s_->layout_minor; }
inline std::uint16_t handle::major_version() const noexcept { return s_->layout_major; }
inline void *handle::base() const noexcept { return s_->hdr; }
inline std::uint64_t handle::size() const noexcept { return s_->size; }

// Removes the name, as shm_unlink does: open handles keep working. Does nothing on Windows, where the
// OS frees the mapping with its last handle.
[[nodiscard]] inline result<void> unlink(std::string_view name) noexcept {
    if (!detail::name_ok(name))
        return unexpected(status::range);
#ifndef _WIN32
    const detail::object_name path = detail::make_name("/", name, "");
    if (shm_unlink(path.data()) != 0)
        return errno == ENOENT ? unexpected(status::not_found) : detail::os_failure();
#endif
    return {};
}

// A copy of the header of the box called name, read without waiting and without the checks of open:
// not_found when nothing under that name has published a header, whatever its layout version.
[[nodiscard]] inline result<header> inspect(std::string_view name) noexcept {
    if (!detail::name_ok(name))
        return unexpected(status::range);
    detail::backoff no_wait(0);
    result<detail::os_mapping> map = detail::map_open(name, no_wait);
    if (!map)
        return unexpected(map.error());
    auto *hdr = static_cast<header *>(map->base());
    if (detail::atomic(hdr->magic).load(std::memory_order_acquire) != magic)
        return unexpected(status::not_found);
    header copy;
    std::memcpy(&copy, hdr, sizeof copy);
    return copy;
}

namespace detail {

// Runs attempt until it succeeds, pausing between tries; false once timeout seconds have passed. With
// Hooks, the wait hooks run only when the first try fails, before the first pause and after the last;
// the large copies run them around everything instead, so they pass false.
template <bool Hooks = true, class F> SHAREDBOX_HOT bool retry(const state &s, double timeout, F &&attempt) {
    if (attempt())
        return true;
    void *hook = nullptr;
    if constexpr (Hooks)
        hook = s.before_wait != nullptr ? s.before_wait() : nullptr;
    backoff wait(timeout);
    bool done = false;
    while (!wait.expired()) {
        wait.pause();
        if (attempt()) {
            done = true;
            break;
        }
    }
    if constexpr (Hooks)
        if (s.after_wait != nullptr)
            s.after_wait(hook);
    return done;
}

// Values of at least this many bytes are copied with read_large and write_large, which run the wait
// hooks around the whole copy, so a caller that releases a lock in them does so for the copy too.
inline constexpr std::size_t large_copy = std::size_t{1} << 20;
inline constexpr std::size_t copy_chunk = std::size_t{4} << 20;

// Copies n bytes while seq stays at before, a chunk at a time; false as soon as it moved, so a reader
// does not finish a copy it would throw away.
inline bool copy_checked(std::byte *dst, const std::byte *src, std::size_t n, std::atomic_ref<std::uint64_t> seq,
                         std::uint64_t before) noexcept {
    for (std::size_t done = 0; done < n;) {
        const std::size_t step = (std::min)(copy_chunk, n - done);
        std::memcpy(dst + done, src + done, step);
        done += step;
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
    }
    return true;
}

// Runs before_wait now and after_wait when it goes out of scope.
class hooks_around {
public:
    explicit hooks_around(const state &s) noexcept
        : s_(s), hook_(s.before_wait != nullptr ? s.before_wait() : nullptr) {}
    ~hooks_around() {
        if (s_.after_wait != nullptr)
            s_.after_wait(hook_);
    }
    hooks_around(const hooks_around &) = delete;
    hooks_around &operator=(const hooks_around &) = delete;

private:
    const state &s_;
    void *hook_;
};

} // namespace detail

inline result<void> handle::set_lock_timeout(seconds timeout) noexcept {
    if (!detail::timeout_ok(timeout, false))
        return unexpected(status::range);
    s_->lock_timeout = timeout.count();
    return {};
}

inline void handle::set_wait_hooks(void *(*before)(), void (*after)(void *)) noexcept {
    s_->before_wait = before;
    s_->after_wait = after;
}

SHAREDBOX_HOT result<read_value> handle::read(std::uint16_t field, std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (field >= s.field_count)
        return unexpected(status::range);
    const field_spec &f = s.fields[field];
    auto seq = detail::atomic(s.hdr->seq);
    read_value out{0, 0};
    const bool done = detail::retry(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        const std::span<const std::byte> src = detail::payload(f, s.record);
        if (!src.empty() && src.size() <= buf.size())
            std::memcpy(buf.data(), src.data(), src.size());
        const std::uint64_t version = detail::atomic(s.counts[field]).load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        out = {src.size(), version};
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return out;
}

inline result<std::uint64_t> handle::read_record(std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (buf.size() < s.record_size)
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t generation = 0;
    const bool done = detail::retry(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        std::memcpy(buf.data(), s.record, s.record_size);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        generation = before >> 1;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return generation;
}

namespace detail {

struct no_hooks {
    explicit no_hooks(const state &) noexcept {}
};

// read_used for one field; Large runs the wait hooks once around the copy and checks the sequence
// number between chunks.
template <bool Large>
result<read_value> read_used_impl(const state &s, std::uint16_t field, std::span<std::byte> buf) {
    const field_spec &f = s.fields[field];
    const type_node &node = s.types.node(s.types.field(field).node);
    auto seq = atomic(s.hdr->seq);
    read_value out{0, 0};
    const std::conditional_t<Large, hooks_around, no_hooks> hooks(s);
    const bool done = retry<!Large>(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        const std::byte *src = s.record + f.offset;
        std::uint32_t length;
        std::memcpy(&length, src, sizeof length);
        // A torn read may see any length; the sequence check below throws that copy away.
        if (length > node.capacity)
            length = node.capacity;
        const std::size_t used = node.slots + std::size_t{length} * node.stride;
        if (used <= buf.size()) {
            if constexpr (Large) {
                if (!copy_checked(buf.data(), src, used, seq, before))
                    return false;
            } else {
                std::memcpy(buf.data(), src, used);
            }
        }
        const std::uint64_t version = atomic(s.counts[field]).load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        out = {used, version};
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return out;
}

} // namespace detail

inline result<read_value> handle::read_used(std::uint16_t field, std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (field >= s.field_count || !detail::is_collection(s.fields[field].kind))
        return unexpected(status::range);
    const detail::type_node &node = s.types.node(s.types.field(field).node);
    if (node.slots + std::uint64_t{node.capacity} * node.stride >= detail::large_copy)
        return detail::read_used_impl<true>(s, field, buf);
    return detail::read_used_impl<false>(s, field, buf);
}

inline result<read_value> handle::read_large(std::uint16_t field, std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (field >= s.field_count)
        return unexpected(status::range);
    const field_spec &f = s.fields[field];
    auto seq = detail::atomic(s.hdr->seq);
    read_value out{0, 0};
    const detail::hooks_around hooks(s);
    const bool done = detail::retry<false>(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        const std::span<const std::byte> src = detail::payload(f, s.record);
        if (src.size() <= buf.size() && !detail::copy_checked(buf.data(), src.data(), src.size(), seq, before))
            return false;
        const std::uint64_t version = detail::atomic(s.counts[field]).load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        out = {src.size(), version};
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return out;
}

inline result<std::uint64_t> handle::read_record_large(std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (buf.size() < s.record_size)
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t generation = 0;
    const detail::hooks_around hooks(s);
    const bool done = detail::retry<false>(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0 || !detail::copy_checked(buf.data(), s.record, s.record_size, seq, before))
            return false;
        generation = before >> 1;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return generation;
}

inline result<void> handle::write_large(std::span<const value> values, seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::values_ok(s, values))
        return unexpected(status::range);
    const detail::hooks_around hooks(s);
    const result<std::uint64_t> locked = lock_with<false>(lock_timeout);
    if (!locked)
        return unexpected(locked.error());
    for (const value &v : values) {
        detail::store(s, v);
        auto count = detail::atomic(s.counts[v.field]);
        count.store(count.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    }
    unlock(*locked);
    detail::wake_waiters(s);
    return {};
}

inline std::span<const std::byte> handle::payload(std::uint16_t field,
                                                  std::span<const std::byte> record) const noexcept {
    return detail::payload(s_->fields[field], record.data());
}

template <bool Hooks> SHAREDBOX_HOT result<std::uint64_t> handle::lock_with(seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::timeout_ok(lock_timeout, false))
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t locked = 0;
    const bool done = detail::retry<Hooks>(s, lock_timeout.count(), [&]() noexcept {
        std::uint64_t even = seq.load(std::memory_order_relaxed);
        if ((even & 1u) != 0 ||
            !seq.compare_exchange_strong(even, even + 1, std::memory_order_acquire, std::memory_order_relaxed))
            return false;
        // Keeps the record stores that follow from moving before the odd sequence number.
        std::atomic_thread_fence(std::memory_order_release);
        detail::atomic(s.hdr->writer_pid).store(detail::current_pid(), std::memory_order_relaxed);
        locked = even;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return locked;
}

SHAREDBOX_HOT result<std::uint64_t> handle::lock(seconds lock_timeout) { return lock_with<true>(lock_timeout); }

SHAREDBOX_HOT void handle::unlock(std::uint64_t locked) noexcept {
    header &h = *s_->hdr;
    // A force_unlock landing between this check and the swap can leave writer_pid cleared; only the
    // message of a later lock timeout depends on it.
    if (detail::atomic(h.seq).load(std::memory_order_relaxed) == locked + 1)
        detail::atomic(h.writer_pid).store(0, std::memory_order_relaxed);
    // If a force_unlock has released this writer's lock, seq has moved on and may belong to another
    // writer's lock, so the swap fails and changes nothing.
    std::uint64_t expected = locked + 1;
    detail::atomic(h.seq).compare_exchange_strong(expected, locked + 2, std::memory_order_release,
                                                  std::memory_order_relaxed);
}

inline result<field_write> handle::begin_write(std::uint16_t field, seconds lock_timeout) {
    detail::state &s = *s_;
    if (field >= s.field_count || s.fields[field].kind != kind_array)
        return unexpected(status::range);
    const result<std::uint64_t> locked = lock(lock_timeout);
    if (!locked)
        return unexpected(locked.error());
    const field_spec &f = s.fields[field];
    return field_write{field, {s.record + f.offset, f.capacity}, *locked};
}

inline void handle::end_write(const field_write &w) noexcept {
    detail::state &s = *s_;
    // Only the lock holder changes a count, so a plain store of the sum is enough.
    auto count = detail::atomic(s.counts[w.field]);
    count.store(count.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    unlock(w.locked);
    detail::wake_waiters(s);
}

SHAREDBOX_HOT result<void> handle::write(std::span<const value> values, seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::values_ok(s, values))
        return unexpected(status::range);
    const result<std::uint64_t> locked = lock(lock_timeout);
    if (!locked)
        return unexpected(locked.error());
    for (const value &v : values) {
        detail::store(s, v);
        // Only the lock holder changes a count, so a plain store of the sum is enough.
        auto count = detail::atomic(s.counts[v.field]);
        count.store(count.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    }
    unlock(*locked);
    detail::wake_waiters(s);
    return {};
}

inline std::uint64_t handle::generation() const noexcept {
    return detail::atomic(s_->hdr->seq).load(std::memory_order_acquire) >> 1;
}

inline std::uint64_t handle::version(std::uint16_t field) const noexcept {
    return detail::atomic(s_->counts[field]).load(std::memory_order_acquire);
}

inline std::uint32_t handle::writer_pid() const noexcept {
    return detail::atomic(s_->hdr->writer_pid).load(std::memory_order_relaxed);
}

inline result<void> handle::force_unlock() noexcept {
    auto seq = detail::atomic(s_->hdr->seq);
    std::uint64_t odd = seq.load(std::memory_order_relaxed);
    if ((odd & 1u) != 0)
        seq.compare_exchange_strong(odd, odd + 1, std::memory_order_release, std::memory_order_relaxed);
    return {};
}

namespace detail {

inline bool owned_test(const state &s, std::uint16_t i) noexcept {
    return (s.owned[i / 64].load(std::memory_order_relaxed) >> (i % 64) & 1u) != 0;
}

inline void owned_set(state &s, std::uint16_t i) noexcept {
    s.owned[i / 64].fetch_or(std::uint64_t{1} << (i % 64), std::memory_order_acq_rel);
}

// Returns whether the bit was set.
inline bool owned_clear(state &s, std::uint16_t i) noexcept {
    const std::uint64_t bit = std::uint64_t{1} << (i % 64);
    return (s.owned[i / 64].fetch_and(~bit, std::memory_order_acq_rel) & bit) != 0;
}

// Whether slot i still records this process: a slot freed by mistake and claimed by another process
// is left to its new owner.
inline bool slot_is_mine(const state &s, std::uint16_t i) noexcept {
    waiter_slot &w = s.slots[i];
    return atomic(w.owner_pid).load(std::memory_order_acquire) == current_pid() &&
           atomic(w.owner_start).load(std::memory_order_acquire) == current_start() &&
           atomic(w.owner_pidns).load(std::memory_order_acquire) == current_pidns();
}

// owner_start while another process frees a dead owner's slot; no real start time has this value.
inline constexpr std::uint64_t start_freeing = UINT64_MAX - 1;

// Frees slot i, which this process claimed and stamped. owner_start goes first, so a process killed at a
// later step leaves no stamped slot for a freer to decrement again. Killed before the pidns store, it leaves an
// unstamped slot of an exited process, which a freer frees without a decrement (the count stays one too high
// if the kill came before the fetch_sub). Killed between the pidns and pid stores, it leaves namespace 0,
// which on Linux counts as alive: the slot is never freed, and the count stays correct. A claimer killed
// between its pid compare-and-swap and its pidns store also leaves namespace 0 and a slot never freed on
// Linux, counted once too many if it had already added itself to waiters.
inline void free_slot(const state &s, std::uint16_t i) noexcept {
    waiter_slot &w = s.slots[i];
    atomic(w.owner_start).store(0, std::memory_order_release);
    atomic(s.hdr->waiters).fetch_sub(1, std::memory_order_seq_cst);
    atomic(w.owner_pidns).store(0, std::memory_order_relaxed);
    atomic(w.owner_pid).store(0, std::memory_order_release);
}

// Does nothing; free_dead_slot calls it after each store so a test can run other steps in between.
struct no_pause {
    void operator()() const noexcept {}
};

// Frees slot w of the exited process pid, seen with owner_start start. Only the process whose compare-and-swap
// sets owner_start to start_freeing goes on, and scanners skip the slot while it holds that value, which it
// keeps until owner_pid is 0; a new claimer's stamp may replace it after that. A start of 0 is a claimer that
// died before stamping: it may or may not have counted itself in waiters, so the slot is freed without a
// decrement and the count can only be too high, which costs a spurious wake-up.
//
// Known limits: a freer killed before its owner_pid store leaves the slot unusable until the segment is created
// again, and counted once too many if it was stamped and the freer was killed before its fetch_sub. The check that
// owner_pid is still pid cannot tell an unstamped claimer from another one with the same pid, which needs the pid
// to be reused within a few instructions.
template <class Pause = no_pause>
inline void free_dead_slot(const state &s, waiter_slot &w, std::uint32_t pid, std::uint64_t start,
                           Pause pause = {}) noexcept {
    if (!atomic(w.owner_start)
             .compare_exchange_strong(start, start_freeing, std::memory_order_acq_rel, std::memory_order_relaxed))
        return;
    // The slot changed hands after the caller read pid. Mainly a stale read, start being the new owner's,
    // which free_dead_waiters's second read of owner_pid narrows; otherwise a new owner with the same start
    // time or none stamped yet. The slot is that owner's.
    if (atomic(w.owner_pid).load(std::memory_order_acquire) != pid) {
        std::uint64_t freeing = start_freeing;
        atomic(w.owner_start)
            .compare_exchange_strong(freeing, start, std::memory_order_release, std::memory_order_relaxed);
        return;
    }
    if (start != 0) {
        atomic(s.hdr->waiters).fetch_sub(1, std::memory_order_seq_cst);
        pause();
    }
    atomic(w.owner_pidns).store(0, std::memory_order_relaxed);
    pause();
    atomic(w.owner_pid).store(0, std::memory_order_release);
    pause();
    // Fails, harmlessly, once a new claimer has stamped its own start.
    std::uint64_t freeing = start_freeing;
    atomic(w.owner_start)
        .compare_exchange_strong(freeing, 0, std::memory_order_release, std::memory_order_relaxed);
    pause();
}

// Frees every slot whose owner has exited.
inline void free_dead_waiters(const state &s) noexcept {
    const std::uint32_t self = current_pid();
    const std::uint64_t own_pidns = current_pidns();
    for (std::uint16_t i = 0; i < s.waiter_slots; ++i) {
        waiter_slot &w = s.slots[i];
        const std::uint32_t pid = atomic(w.owner_pid).load(std::memory_order_acquire);
        if (pid == 0)
            continue;
        std::uint64_t start = atomic(w.owner_start).load(std::memory_order_acquire);
#ifdef _WIN32
        static_cast<void>(own_pidns);
        const bool checkable = true;
#else
        // A pid means something only inside its namespace: a slot of another namespace, or with either
        // namespace unknown (0), counts as alive. Windows has no pid namespaces.
        const std::uint64_t pidns = atomic(w.owner_pidns).load(std::memory_order_acquire);
        const bool checkable = pidns != 0 && own_pidns != 0 && pidns == own_pidns;
#endif
        // The slot changed hands between the reads: start and pidns may be the new owner's, not pid's.
        if (atomic(w.owner_pid).load(std::memory_order_acquire) != pid)
            continue;
        // start 0: not stamped yet, so only whether the pid runs at all can be checked.
        if (start == start_freeing || !checkable || (pid == self && (start == 0 || start == current_start())) ||
            process_alive(pid, start == 0 ? start_unknown : start))
            continue;
        free_dead_slot(s, w, pid, start);
    }
}

} // namespace detail

inline result<std::uint16_t> handle::register_waiter() {
    detail::state &s = *s_;
    const std::uint32_t pid = detail::current_pid();
    const std::uint64_t start = detail::current_start();
    const std::uint64_t pidns = detail::current_pidns();
    detail::free_dead_waiters(s);
    for (std::uint16_t i = 0; i < s.waiter_slots; ++i) {
        waiter_slot &w = s.slots[i];
        std::uint32_t none = 0;
        if (!detail::atomic(w.owner_pid)
                 .compare_exchange_strong(none, pid, std::memory_order_acq_rel, std::memory_order_relaxed))
            continue;
        // Counted before owner_start is stamped: a freer decrements only for a stamped slot, so a claimer
        // killed in between leaves waiters too high, never too low.
        detail::atomic(s.hdr->waiters).fetch_add(1, std::memory_order_seq_cst);
        detail::atomic(w.interrupt).store(0, std::memory_order_relaxed);
        detail::atomic(w.owner_pidns).store(pidns, std::memory_order_relaxed);
        detail::atomic(w.owner_start).store(start, std::memory_order_release);
        detail::owned_set(s, i);
#ifdef _WIN32
        HANDLE e = detail::event(s, i);
        if (e == nullptr) {
            const unexpected failed = detail::os_failure();
            detail::owned_clear(s, i);
            detail::free_slot(s, i);
            return failed;
        }
        // Drops a wake-up meant for an earlier owner of the slot.
        ResetEvent(e);
#endif
        return i;
    }
    return unexpected(status::no_slot);
}

inline void handle::release_waiter(std::uint16_t slot) noexcept {
    detail::state &s = *s_;
    if (slot < s.waiter_slots && detail::owned_clear(s, slot) && detail::slot_is_mine(s, slot))
        detail::free_slot(s, slot);
}

inline bool handle::waiter_held(std::uint16_t slot) const noexcept {
    return slot < s_->waiter_slots && detail::owned_test(*s_, slot) && detail::slot_is_mine(*s_, slot);
}

inline std::uint32_t handle::waiters() const noexcept {
    return detail::atomic(s_->hdr->waiters).load(std::memory_order_acquire);
}

namespace detail {

#ifdef _WIN32
// The auto-reset event of waiter slot i, opened on first use and kept until the handle is destroyed.
inline HANDLE event(const state &s, std::uint16_t slot) noexcept {
    std::atomic<HANDLE> &cell = s.events[slot];
    HANDLE e = cell.load(std::memory_order_acquire);
    if (e != nullptr)
        return e;
    char suffix[8];
    std::snprintf(suffix, sizeof suffix, ".w%u", static_cast<unsigned>(slot));
    const wide_name wide = make_wide_name(s.name, suffix);
    e = CreateEventW(nullptr, FALSE, FALSE, wide.data());
    if (e == nullptr)
        return nullptr;
    HANDLE previous = nullptr;
    if (!cell.compare_exchange_strong(previous, e, std::memory_order_acq_rel, std::memory_order_acquire)) {
        CloseHandle(e);
        return previous;
    }
    return e;
}
#endif

SHAREDBOX_HOT void wake_waiters(const state &s) noexcept {
    header &h = *s.hdr;
    // seq_cst, not only release: the load of waiters below must not move before this increment or the
    // swap of seq, or a waiter registering at the same time could be missed.
    atomic(h.wake_word).fetch_add(1, std::memory_order_seq_cst);
    // waiters is never below the number of claimed slots, so 0 means no one to wake.
    if (atomic(h.waiters).load(std::memory_order_seq_cst) == 0)
        return;
#ifdef _WIN32
    for (std::uint16_t i = 0; i < s.waiter_slots; ++i)
        if (atomic(s.slots[i].owner_pid).load(std::memory_order_seq_cst) != 0)
            if (HANDLE e = event(s, i); e != nullptr)
                SetEvent(e);
#else
    syscall(SYS_futex, &h.wake_word, futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
}

} // namespace detail

inline result<wake> handle::wait(std::uint16_t slot, std::uint64_t last_generation, seconds timeout) {
    detail::state &s = *s_;
    if (slot >= s.waiter_slots || !detail::owned_test(s, slot) || !detail::timeout_ok(timeout, true))
        return unexpected(status::range);
    header &h = *s.hdr;
    const detail::clock::time_point deadline =
        detail::clock::now() + std::chrono::duration_cast<detail::clock::duration>(timeout);
    for (;;) {
        // The word is read before the generation: a write between the two changes the word, and the
        // futex then refuses to sleep.
        const std::uint32_t word = detail::atomic(h.wake_word).load(std::memory_order_seq_cst);
        if (detail::atomic(h.seq).load(std::memory_order_seq_cst) >> 1 != last_generation)
            return wake::changed;
        std::uint32_t set = 1;
        if (detail::atomic(s.slots[slot].interrupt)
                .compare_exchange_strong(set, 0, std::memory_order_acq_rel, std::memory_order_relaxed))
            return wake::interrupted;
        const detail::clock::duration remaining = deadline - detail::clock::now();
        if (remaining <= detail::clock::duration::zero())
            return unexpected(status::timeout);
#ifdef _WIN32
        static_cast<void>(word);
        HANDLE e = detail::event(s, slot);
        if (e == nullptr)
            return detail::os_failure();
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
        if (WaitForSingleObject(e, static_cast<DWORD>(ms) + 1) == WAIT_FAILED)
            return detail::os_failure();
#else
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(remaining).count();
        timespec ts{};
        ts.tv_sec = static_cast<time_t>(ns / 1000000000);
        ts.tv_nsec = static_cast<long>(ns % 1000000000);
        syscall(SYS_futex, &h.wake_word, detail::futex_wait, word, &ts, nullptr, 0);
#endif
    }
}

inline result<void> handle::interrupt(std::uint16_t slot) {
    detail::state &s = *s_;
    if (slot >= s.waiter_slots)
        return unexpected(status::range);
    detail::atomic(s.slots[slot].interrupt).store(1, std::memory_order_seq_cst);
#ifdef _WIN32
    HANDLE e = detail::event(s, slot);
    if (e == nullptr)
        return detail::os_failure();
    SetEvent(e);
#else
    detail::atomic(s.hdr->wake_word).fetch_add(1, std::memory_order_seq_cst);
    syscall(SYS_futex, &s.hdr->wake_word, detail::futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
    return {};
}

namespace detail {

inline void release_owned(sbx_handle *h) noexcept {
    delete static_cast<handle *>(h->private_data);
    h->release = nullptr;
    h->private_data = nullptr;
    h->base = nullptr;
}

// Moves h to the heap and fills out with a struct that owns it. On failure h is left as it was.
inline status export_into(handle &&h, sbx_handle &out) noexcept {
    auto *owner = new (std::nothrow) handle(std::move(h));
    if (owner == nullptr)
        return status::os;
    out.layout_major = owner->major_version();
    out.layout_minor = owner->minor_version();
    out.handle_version = handle_version;
    out.base = owner->base();
    out.size = owner->size();
    out.name = owner->name().data();
    out.release = release_owned;
    out.private_data = owner;
    return status::ok;
}

} // namespace detail

inline result<handle> handle::from_capsule(sbx_handle *capsule) {
    if (capsule == nullptr || capsule->release == nullptr || capsule->base == nullptr ||
        capsule->handle_version < 1 || capsule->name == nullptr)
        return unexpected(status::range);
    std::size_t length = 0;
    while (length <= name_max && capsule->name[length] != '\0')
        ++length;
    const std::string_view name(capsule->name, length);
    if (!detail::name_ok(name))
        return unexpected(status::range);
    auto *hdr = static_cast<header *>(capsule->base);
    if (capsule->size < page_size || detail::atomic(hdr->magic).load(std::memory_order_acquire) != magic)
        return unexpected(status::corrupt);
    const header line0 = detail::copy_line0(hdr);
    if (!detail::major_ok(line0.layout_major))
        return unexpected(status::layout);
    if (const status rc = detail::check_geometry(line0, capsule->size); rc != status::ok)
        return unexpected(rc);
    std::unique_ptr<detail::state> s = detail::make_state(name, line0.field_count, line0.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    s->record_size = line0.record_size;
    if (const status rc = detail::copy_fields(*s, hdr, line0); rc != status::ok)
        return unexpected(rc);
    s->record_offset = line0.record;
    s->schema_hash = line0.schema_hash;
    s->create_id = line0.create_id;
    s->foreign = *capsule;
    capsule->release = nullptr;
    detail::bind(*s, s->foreign.base, s->foreign.size, line0.layout_major, line0.layout_minor);
    return handle(s.release());
}

inline result<handle> handle::duplicate() const {
    const detail::state &src = *s_;
    if (src.foreign.release != nullptr)
        return unexpected(status::range);
#ifdef _WIN32
    HANDLE os = nullptr;
    if (!DuplicateHandle(GetCurrentProcess(), src.map.os(), GetCurrentProcess(), &os, 0, FALSE,
                         DUPLICATE_SAME_ACCESS))
        return detail::os_failure();
    void *base = MapViewOfFile(os, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    if (base == nullptr) {
        const unexpected failed = detail::os_failure();
        CloseHandle(os);
        return failed;
    }
#else
    const int os = fcntl(src.map.os(), F_DUPFD_CLOEXEC, 0);
    if (os < 0)
        return detail::os_failure();
    void *base = mmap(nullptr, static_cast<std::size_t>(src.size), PROT_READ | PROT_WRITE, MAP_SHARED, os, 0);
    if (base == MAP_FAILED) {
        const unexpected failed = detail::os_failure();
        close(os);
        return failed;
    }
#endif
    detail::os_mapping map = detail::os_mapping::adopt(os, base, src.size);
    std::unique_ptr<detail::state> s = detail::make_state(src.name, src.field_count, src.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    std::copy_n(src.fields.get(), src.field_count, s->fields.get());
    if (const status rc = src.types.copy(s->types); rc != status::ok)
        return unexpected(rc);
    s->record_size = src.record_size;
    s->record_offset = src.record_offset;
    s->schema_hash = src.schema_hash;
    s->create_id = src.create_id;
    s->lock_timeout = src.lock_timeout;
    s->map = std::move(map);
    detail::bind(*s, s->map.base(), src.size, src.layout_major, src.layout_minor);
    return handle(s.release());
}

inline sbx_handle *handle::to_capsule() && {
    std::unique_ptr<sbx_handle> out(new (std::nothrow) sbx_handle{});
    if (out == nullptr || detail::export_into(std::move(*this), *out) != status::ok)
        return nullptr;
    return out.release();
}

} // namespace v3
} // namespace sharedbox

#undef SHAREDBOX_HOT
#ifdef SHAREDBOX_DEFINED_NOMINMAX
#undef NOMINMAX
#undef SHAREDBOX_DEFINED_NOMINMAX
#endif
#ifdef SHAREDBOX_DEFINED_WIN32_LEAN_AND_MEAN
#undef WIN32_LEAN_AND_MEAN
#undef SHAREDBOX_DEFINED_WIN32_LEAN_AND_MEAN
#endif

#endif
