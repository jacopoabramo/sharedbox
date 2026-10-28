// core.hpp: what every kind of sharedbox segment uses: the result and error types, process
// liveness, object names, OS mappings, the type codec and the metadata area.
//
// C++20, header-only, 64-bit little-endian targets, no exceptions. Names in sharedbox::detail are
// internal and may change in any release. This header leaves SHAREDBOX_HOT and the Windows macros
// defined; only sharedbox.hpp removes them, so a file that includes core.hpp directly should include
// sharedbox.hpp last or accept that.
#ifndef SHAREDBOX_CORE_HPP
#define SHAREDBOX_CORE_HPP

#include <sharedbox/sharedbox_c.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <complex>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <limits>
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

inline constexpr std::size_t name_max = 240;
inline constexpr std::uint32_t max_fields = 256;
inline constexpr std::uint32_t max_capacity = std::uint32_t{1} << 20;
inline constexpr std::uint32_t max_waiter_slots = 4096;
inline constexpr std::uint16_t default_waiter_slots = 64;
inline constexpr std::uint32_t page_size = 4096;
inline constexpr double max_timeout = 86400.0;
inline constexpr std::uint32_t kind_shift = 24;
inline constexpr std::uint32_t capacity_mask = (std::uint32_t{1} << kind_shift) - 1;
// A description's offset is the low 24 bits of a field entry, so the table holds at most 16 MiB.
inline constexpr std::uint32_t max_types_size = capacity_mask + 1;
inline constexpr std::uint32_t max_type_depth = 16;

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
inline constexpr std::uint8_t kind_int8 = 13;
inline constexpr std::uint8_t kind_int16 = 14;
inline constexpr std::uint8_t kind_int32 = 15;
inline constexpr std::uint8_t kind_uint8 = 16;
inline constexpr std::uint8_t kind_uint16 = 17;
inline constexpr std::uint8_t kind_uint32 = 18;
inline constexpr std::uint8_t kind_uint64 = 19;
inline constexpr std::uint8_t kind_float16 = 20;
inline constexpr std::uint8_t kind_float32 = 21;
inline constexpr std::uint8_t kind_complex64 = 22;
inline constexpr std::uint8_t kind_datetime64 = 23;
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
// An array of a stream item that stores its own shape, up to the maximum its description gives.
inline constexpr std::uint8_t kind_max_array = 75;

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
    foreign = -12,
    busy = -13,
    ended = -14,
    interrupted = -15,
    released = -16,
    metadata_full = -17,
};

using seconds = std::chrono::duration<double>;

enum class wake { changed, interrupted };

struct waiter_slot {
    std::uint64_t owner_start;
    std::uint64_t owner_pidns;
    std::uint32_t owner_pid;
    std::uint32_t interrupt;
    // The offset from the mapping's start of the count the current wait in this slot added 1 to; 0 when
    // none.
    std::uint32_t asleep_on;
    std::uint32_t reserved;
};

// The stored value of a kind_ref field: which box it refers to. All zero when empty; create never gives
// a create_id of 0. name is NUL-padded and ends at the first NUL or after name_max bytes.
struct box_ref {
    std::uint64_t create_id;
    std::uint64_t schema_hash;
    char name[name_max];
};

// The head of one description in the table of a box segment. A description is this head, a body
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

static_assert(std::is_standard_layout_v<waiter_slot> && std::is_trivially_copyable_v<waiter_slot>);
static_assert(std::is_standard_layout_v<box_ref> && std::is_trivially_copyable_v<box_ref>);
static_assert(sizeof(waiter_slot) == 32 && alignof(waiter_slot) == 8);
static_assert(offsetof(waiter_slot, owner_start) == 0);
static_assert(offsetof(waiter_slot, owner_pidns) == 8);
static_assert(offsetof(waiter_slot, owner_pid) == 16);
static_assert(offsetof(waiter_slot, interrupt) == 20);
static_assert(offsetof(waiter_slot, asleep_on) == 24 && offsetof(waiter_slot, reserved) == 28);
static_assert(sizeof(box_ref) == 256 && alignof(box_ref) == 8);
static_assert(offsetof(box_ref, create_id) == 0);
static_assert(offsetof(box_ref, schema_hash) == 8);
static_assert(offsetof(box_ref, name) == 16);
// Only lock-free atomics work on memory shared between processes: a fallback that takes a lock would
// keep that lock in one process. With Clang's libc++, std::atomic_ref needs libc++ 19 or later.
static_assert(std::atomic_ref<std::uint32_t>::is_always_lock_free);
static_assert(std::atomic_ref<std::uint64_t>::is_always_lock_free);
namespace detail {
inline constexpr std::size_t align32 = std::atomic_ref<std::uint32_t>::required_alignment;
inline constexpr std::size_t align64 = std::atomic_ref<std::uint64_t>::required_alignment;
} // namespace detail
static_assert(offsetof(waiter_slot, owner_start) % detail::align64 == 0 &&
              offsetof(waiter_slot, owner_pidns) % detail::align64 == 0);
static_assert(offsetof(waiter_slot, owner_pid) % detail::align32 == 0 &&
              offsetof(waiter_slot, interrupt) % detail::align32 == 0 &&
              offsetof(waiter_slot, asleep_on) % detail::align32 == 0);

static_assert(SBX_OK == int(status::ok) && SBX_E_EXISTS == int(status::exists));
static_assert(SBX_E_NOT_FOUND == int(status::not_found) && SBX_E_LAYOUT == int(status::layout));
static_assert(SBX_E_SCHEMA == int(status::schema) && SBX_E_CORRUPT == int(status::corrupt));
static_assert(SBX_E_LOCK_TIMEOUT == int(status::lock_timeout) && SBX_E_TIMEOUT == int(status::timeout));
static_assert(SBX_E_NO_SLOT == int(status::no_slot) && SBX_E_RANGE == int(status::range));
static_assert(SBX_E_OS == int(status::os) && SBX_E_KIND == int(status::kind_mismatch));
static_assert(SBX_E_FOREIGN == int(status::foreign));
static_assert(SBX_E_BUSY == int(status::busy) && SBX_E_ENDED == int(status::ended) &&
              SBX_E_INTERRUPTED == int(status::interrupted));
static_assert(SBX_E_METADATA_FULL == int(status::metadata_full));

// Why a call failed: the status, the OS error (errno or GetLastError()) read where the OS call failed,
// and what the call found when that is the reason: the magic for kind_mismatch and foreign, for layout
// core_major << 48 | core_minor << 32 | kind_major << 16 | kind_minor of the segment's first line.
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
// Free functions, not constructors of result: a constructor under this #if would give the class a
// different body in C++20 and C++23 translation units of one program. A free to_expected also avoids
// expected's own converting constructor reading a result<bool> through its explicit operator bool, and
// a member conversion cannot move a move-only value.
template <class T> constexpr std::expected<T, error> to_expected(result<T> r) {
    if (!r)
        return std::unexpected(r.error());
    if constexpr (std::is_void_v<T>)
        return {};
    else
        return *std::move(r);
}

template <class T> constexpr result<T> from_expected(std::expected<T, error> e) {
    if (!e)
        return unexpected(e.error());
    return result<T>(std::move(*e));
}

constexpr result<void> from_expected(std::expected<void, error> e) {
    if (!e)
        return unexpected(e.error());
    return {};
}
#endif

namespace detail {

// Shared words are plain integers in the mapping; every access to them goes through std::atomic_ref.
template <class T> SHAREDBOX_HOT std::atomic_ref<T> atomic(T &word) noexcept { return std::atomic_ref<T>(word); }

} // namespace detail

inline constexpr std::uint16_t core_major = 1;
inline constexpr std::uint16_t core_minor = 0;
// One magic per kind of segment, "SBX_BOX_" and "SBX_STRM" read as little-endian u64. The magic is
// stored last when a segment is created, so a nonzero magic also means it is published.
inline constexpr std::uint64_t box_magic = 0x5F584F425F584253;
inline constexpr std::uint64_t stream_magic = 0x4D5254535F584253;

// Line 0 of every segment, whatever its kind, written once by the creator.
struct common_header {
    std::uint64_t magic;
    std::uint16_t core_major;
    std::uint16_t core_minor;
    std::uint16_t kind_major;
    std::uint16_t kind_minor;
    std::uint64_t schema_hash;
    std::uint64_t create_id;
    std::uint64_t creator_start;
    std::uint64_t creator_pidns;
    std::uint32_t creator_pid;
    std::uint16_t waiter_slots;
    std::uint16_t reserved0;
    std::uint64_t size;
};
static_assert(std::is_standard_layout_v<common_header> && std::is_trivially_copyable_v<common_header>);
static_assert(sizeof(common_header) == 64 && alignof(common_header) == 8);
static_assert(offsetof(common_header, magic) == 0 && offsetof(common_header, core_major) == 8 &&
              offsetof(common_header, core_minor) == 10 && offsetof(common_header, kind_major) == 12 &&
              offsetof(common_header, kind_minor) == 14 && offsetof(common_header, schema_hash) == 16 &&
              offsetof(common_header, create_id) == 24 && offsetof(common_header, creator_start) == 32 &&
              offsetof(common_header, creator_pidns) == 40 && offsetof(common_header, creator_pid) == 48 &&
              offsetof(common_header, waiter_slots) == 52 && offsetof(common_header, reserved0) == 54 &&
              offsetof(common_header, size) == 56);
static_assert(offsetof(common_header, magic) % detail::align64 == 0);

namespace detail {

// The kind a magic names, for messages; empty for a magic this build does not know.
inline std::string_view kind_name(std::uint64_t magic) noexcept {
    switch (magic) {
    case box_magic:
        return "box";
    case stream_magic:
        return "stream";
    default:
        return {};
    }
}

} // namespace detail

// What a segment layout gives the core's open step: its header type, which starts with the common line,
// its magic, the kind major versions it reads, a copy of the header that leaves out the words other
// processes change, and a check of the kind's own geometry once the common line passed.
template <class L>
concept segment_layout = requires(const typename L::header_type &h, const void *base, std::uint64_t mapped) {
    { L::magic } -> std::convertible_to<std::uint64_t>;
    { L::oldest_major } -> std::convertible_to<std::uint16_t>;
    { L::newest_major } -> std::convertible_to<std::uint16_t>;
    { h.common } -> std::convertible_to<const common_header &>;
    { L::copy_header(base) } -> std::same_as<typename L::header_type>;
    { L::check_geometry(h, mapped) } -> std::same_as<status>;
};

namespace detail {

// Line 0 at base, copied once so that only the copy is checked and used.
inline common_header copy_common(const void *base) noexcept {
    common_header line0{};
    std::memcpy(&line0, base, sizeof line0);
    return line0;
}

inline std::uint64_t layout_found(const common_header &line0) noexcept {
    return std::uint64_t{line0.core_major} << 48 | std::uint64_t{line0.core_minor} << 32 |
           std::uint64_t{line0.kind_major} << 16 | line0.kind_minor;
}

// Checks the copy h of the header of a published segment of mapped bytes as a segment of kind L: the
// kind, the core version, the kind's version, then the kind's geometry. Fills found with what refused it.
template <segment_layout L>
result<common_header> open_check(const typename L::header_type &h, std::uint64_t mapped) noexcept {
    const common_header &line0 = h.common;
    if (line0.magic != L::magic)
        return unexpected(
            error{kind_name(line0.magic).empty() ? status::foreign : status::kind_mismatch, 0, line0.magic});
    if (line0.core_major != core_major)
        return unexpected(error{status::layout, 0, layout_found(line0)});
    if (line0.kind_major < L::oldest_major || line0.kind_major > L::newest_major)
        return unexpected(error{status::layout, 0, layout_found(line0)});
    if (line0.size != mapped || line0.waiter_slots == 0 || line0.waiter_slots > max_waiter_slots)
        return unexpected(status::corrupt);
    if (const status rc = L::check_geometry(h, mapped); rc != status::ok)
        return unexpected(rc);
    return line0;
}

// Copies the header of a published segment of mapped bytes and checks the copy as kind L. A mapping
// smaller than a page is refused before any of it is read, so no kind copies a header past the end.
template <segment_layout L>
result<typename L::header_type> open_header(const void *base, std::uint64_t mapped) noexcept {
    static_assert(sizeof(typename L::header_type) <= page_size);
    if (mapped < page_size)
        return unexpected(status::corrupt);
    const typename L::header_type h = L::copy_header(base);
    if (const result<common_header> line0 = open_check<L>(h, mapped); !line0)
        return unexpected(line0.error());
    return h;
}

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

// Whether the process that recorded pid, start and pidns has exited, as far as this process can tell. A
// pid means something only inside its namespace: one of another namespace, or with either namespace
// unknown (0), counts as alive, as does this process. start 0 means not stamped yet, so only whether the
// pid runs at all is checked. Windows has no pid namespaces.
inline bool owner_exited(std::uint32_t pid, std::uint64_t start, std::uint64_t pidns) noexcept {
#ifdef _WIN32
    static_cast<void>(pidns);
#else
    const std::uint64_t own = current_pidns();
    if (pidns == 0 || own == 0 || pidns != own)
        return false;
#endif
    if (pid == current_pid() && (start == 0 || start == current_start()))
        return false;
    return !process_alive(pid, start == 0 ? start_unknown : start);
}

// "Local\SBX:" (10), a name of name_max, "#w4095" (6) and the terminating NUL.
inline constexpr std::size_t object_name_max = 264;
static_assert(10 + name_max + 6 + 1 <= object_name_max && object_name_max % 8 == 0);
// MAX_PATH on Windows, NAME_MAX on Linux.
static_assert(10 + name_max + 6 <= 260 && 4 + name_max <= 255);
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
    return kind <= kind_datetime64 || (kind >= kind_enum && kind <= kind_max_array);
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
    case kind_uint64:
    case kind_datetime64:
        return 8u;
    case kind_str:
    case kind_bytes:
    case kind_decimal:
    case kind_date:
    case kind_timedelta:
    case kind_int32:
    case kind_uint32:
    case kind_float32:
    case kind_complex64:
        return 4u;
    case kind_int16:
    case kind_uint16:
    case kind_float16:
        return 2u;
    default:
        return 1u;
    }
}

// The capacity every field of a fixed-size kind has; 0 for prefixed, described and unknown kinds.
inline std::uint32_t fixed_capacity(std::uint32_t kind) noexcept {
    switch (kind) {
    case kind_bool:
    case kind_int8:
    case kind_uint8:
        return 1u;
    case kind_int16:
    case kind_uint16:
    case kind_float16:
        return 2u;
    case kind_int32:
    case kind_uint32:
    case kind_float32:
        return 4u;
    case kind_int:
    case kind_float:
    case kind_uint64:
    case kind_complex64:
    case kind_datetime64:
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

inline std::uint64_t round_up(std::uint64_t value, std::uint64_t unit) noexcept {
    return (value + unit - 1) / unit * unit;
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
    // Copies table and checks it with the field entries, capacity_and_kind as stored. max_arrays lets a
    // max_array be an entry's type, or a member reached from one through records and tuples alone, as a
    // stream item's table may hold it. On failure the table is left empty.
    status parse(std::span<const std::byte> table, std::span<const std::uint32_t> entries,
                 bool max_arrays = false) noexcept;
    status copy(type_table &out) const noexcept {
        return out.parse(bytes(), {entries_.get(), field_count_}, max_arrays_);
    }

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
    bool max_arrays_ = false;
    std::uint32_t node_count_ = 0;
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
    type_parser(std::span<const std::byte> table, std::uint64_t *seen, type_table *out, bool max_arrays) noexcept
        : table_(table), seen_(seen), out_(out), max_arrays_(max_arrays) {}

    type_counts counts;

    status field(std::uint32_t entry, type_ref &out) noexcept {
        const auto kind = static_cast<std::uint8_t>(entry >> kind_shift);
        const std::uint32_t low = entry & capacity_mask;
        if (!described(kind))
            return fixed(kind, low, true, out);
        return describe(kind, low, no_parent, 1, true, true, max_arrays_, out);
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

    status member(const std::byte *entry, std::uint32_t parent, std::uint32_t depth, bool array_ok, bool max_ok,
                  type_member &out) noexcept {
        std::uint32_t value = 0;
        std::memcpy(&out.offset, entry, sizeof out.offset);
        std::memcpy(&value, entry + 4, sizeof value);
        const auto kind = static_cast<std::uint8_t>(value >> kind_shift);
        const std::uint32_t low = value & capacity_mask;
        if (!described(kind))
            return fixed(kind, low, false, out.type);
        return describe(kind, low, parent, depth + 1, false, array_ok, max_ok, out.type);
    }

    status describe(std::uint8_t kind, std::uint32_t at, std::uint32_t parent, std::uint32_t depth, bool top,
                    bool array_ok, bool max_ok, type_ref &out) noexcept;
    status place(std::uint8_t kind, const type_head &head, type_node &node, type_ref &out) noexcept;

    std::span<const std::byte> table_;
    std::uint64_t *seen_;
    type_table *out_;
    bool max_arrays_;
};

inline status type_parser::describe(std::uint8_t kind, std::uint32_t at, std::uint32_t parent, std::uint32_t depth,
                                    bool top, bool array_ok, bool max_ok, type_ref &out) noexcept {
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
    if (head.flags != 0 || (kind == kind_array && !array_ok) || (kind == kind_max_array && !max_ok))
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
    // Only a record's or tuple's members may be max_arrays, and only when the record itself may be one.
    const bool members_take_max_arrays = max_ok && (kind == kind_record || kind == kind_tuple);
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
        members_take_arrays = kind == kind_record || kind == kind_tuple || kind == kind_optional;
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
    case kind_array:
    case kind_max_array: {
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
        // A max_array's data starts with the 64-byte line of its stored shape.
        ok = ok && total + (kind == kind_max_array ? 64 : 0) == head.size;
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
        if (const status rc =
                member(entries + std::size_t{i} * 8, at, depth, members_take_arrays, members_take_max_arrays, m);
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
    case kind_max_array:
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
        return m[0].offset == node.slots &&
                       size == round_up(node.slots + std::uint64_t{node.capacity} * node.stride, a)
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
        return size == round_up(node.slots + std::uint64_t{node.capacity} * node.stride, a) ? status::ok
                                                                                            : status::corrupt;
    }
    default:
        return status::corrupt;
    }
}

inline status type_table::parse(std::span<const std::byte> table, std::span<const std::uint32_t> entries,
                                bool max_arrays) noexcept {
    *this = type_table{};
    if (table.size() % 8 != 0 || table.size() > max_types_size || entries.size() > max_fields)
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
    max_arrays_ = max_arrays;
    const std::span<const std::byte> copy(bytes_.get(), size);
    type_parser counter(copy, seen.get(), nullptr, max_arrays);
    for (std::uint16_t i = 0; i < count; ++i) {
        type_ref ignored;
        if (const status rc = counter.field(entries[i], ignored); rc != status::ok) {
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
    type_parser filler(copy, seen.get(), this, max_arrays);
    for (std::uint16_t i = 0; i < count; ++i)
        if (const status rc = filler.field(entries[i], fields_[i]); rc != status::ok) {
            *this = type_table{};
            return rc;
        }
    return status::ok;
}

// The bytes of the elements a max_array value holds, from the stored shape in its first 64 bytes at line;
// UINT64_MAX when a dimension is above its maximum, which a torn copy or a forged slot can show. t is a
// max_array of table.
inline std::uint64_t max_array_bytes(const type_table &table, const type_ref &t, const std::byte *line) noexcept {
    const type_node &node = table.node(t.node);
    std::uint64_t total = node.dtype.bits / 8;
    for (std::uint32_t i = 0; i < node.ndim; ++i) {
        std::uint64_t dim = 0;
        std::memcpy(&dim, line + std::size_t{i} * 8, sizeof dim);
        if (dim > table.number(node.numbers + i))
            return UINT64_MAX;
        // Each dimension is at most its maximum, whose product checked below 2**32, so this cannot overflow.
        total *= dim;
    }
    return total;
}

// One or more segments of [A-Za-z0-9_-] joined by ':', at most name_max characters. '.' is kept for a
// later name.field address, and '#' for the suffix of Windows waiter events.
inline bool name_ok(std::string_view name) noexcept {
    if (name.empty() || name.size() > name_max || name.front() == ':' || name.back() == ':')
        return false;
    char previous = '\0';
    for (const char c : name) {
        if (c == ':') {
            if (previous == ':')
                return false;
        } else if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' ||
                     c == '-')) {
            return false;
        }
        previous = c;
    }
    return true;
}

using object_name = std::array<char, object_name_max>;

// name must have passed name_ok.
inline object_name make_name(const char *prefix, std::string_view name, const char *suffix) noexcept {
    object_name out{};
    std::snprintf(out.data(), out.size(), "%sSBX:%.*s%s", prefix, static_cast<int>(name.size()), name.data(),
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

// Waits for the creator of the segment at base to publish it: true once its magic is nonzero, false when
// wait expires first.
inline bool wait_published(void *base, backoff &wait) noexcept {
    auto &magic = static_cast<common_header *>(base)->magic;
    while (atomic(magic).load(std::memory_order_acquire) == 0) {
        if (wait.expired())
            return false;
        wait.pause();
    }
    return true;
}

// One process's view of a segment's waiter slot table: the slots and the count of claimed ones, both in
// the mapping; the mapping's start and the offsets from it of the counts a slot's asleep_on may name, 0
// marking an unused entry; which slots this process claimed; and on Windows each slot's event. The owner
// sets the pointers and offsets once the segment is mapped and calls release_owned_slots before unmapping it.
struct waiter_table {
    waiter_slot *slots = nullptr;
    std::uint32_t *claimed = nullptr;
    std::byte *base = nullptr;
    std::array<std::uint32_t, 2> sleeper_counts{};
    std::uint16_t count = 0;
    std::array<std::atomic<std::uint64_t>, max_waiter_slots / 64> owned{};
#ifdef _WIN32
    // The segment's name, NUL-terminated, kept by the table's owner; events are named after it.
    const char *name = nullptr;
    std::unique_ptr<std::atomic<HANDLE>[]> events;

    ~waiter_table() {
        if (events != nullptr)
            for (std::uint16_t i = 0; i < count; ++i)
                if (HANDLE e = events[i].load(std::memory_order_relaxed); e != nullptr)
                    CloseHandle(e);
    }
#endif
};

#ifdef _WIN32
// The auto-reset event of waiter slot i, opened on first use and kept until the table is destroyed.
inline HANDLE event(const waiter_table &t, std::uint16_t slot) noexcept {
    std::atomic<HANDLE> &cell = t.events[slot];
    HANDLE e = cell.load(std::memory_order_acquire);
    if (e != nullptr)
        return e;
    char suffix[8];
    std::snprintf(suffix, sizeof suffix, "#w%u", static_cast<unsigned>(slot));
    const wide_name wide = make_wide_name(t.name, suffix);
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

inline bool owned_test(const waiter_table &t, std::uint16_t i) noexcept {
    return (t.owned[i / 64].load(std::memory_order_relaxed) >> (i % 64) & 1u) != 0;
}

inline void owned_set(waiter_table &t, std::uint16_t i) noexcept {
    t.owned[i / 64].fetch_or(std::uint64_t{1} << (i % 64), std::memory_order_acq_rel);
}

// Returns whether the bit was set.
inline bool owned_clear(waiter_table &t, std::uint16_t i) noexcept {
    const std::uint64_t bit = std::uint64_t{1} << (i % 64);
    return (t.owned[i / 64].fetch_and(~bit, std::memory_order_acq_rel) & bit) != 0;
}

// Whether slot i still records this process: a slot freed by mistake and claimed by another process
// is left to its new owner.
inline bool slot_is_mine(const waiter_table &t, std::uint16_t i) noexcept {
    waiter_slot &w = t.slots[i];
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
// Linux, counted once too many if it had already added itself to the count.
inline void free_slot(const waiter_table &t, std::uint16_t i) noexcept {
    waiter_slot &w = t.slots[i];
    atomic(w.owner_start).store(0, std::memory_order_release);
    atomic(*t.claimed).fetch_sub(1, std::memory_order_seq_cst);
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
// died before stamping: it may or may not have counted itself in the count, so the slot is freed without a
// decrement and the count can only be too high, which costs a spurious wake-up.
//
// Known limits: a freer killed before its owner_pid store leaves the slot unusable until the segment is created
// again, and counted once too many if it was stamped and the freer was killed before its fetch_sub; a freer killed
// after it won the slot and before its subtraction of the sleeper count leaves that count one too high and the
// slot stuck with the freeing marker; an owner killed between its exit exchange of asleep_on and its fetch_sub
// leaves the count one too high. The
// check that owner_pid is still pid cannot tell an unstamped claimer from another one with the same pid, which
// needs the pid to be reused within a few instructions.
template <class Pause = no_pause>
inline void free_dead_slot(const waiter_table &t, waiter_slot &w, std::uint32_t pid, std::uint64_t start,
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
        atomic(*t.claimed).fetch_sub(1, std::memory_order_seq_cst);
        pause();
    }
    // The owner died inside a wait: take back the 1 it added. An offset the kind never names is left alone,
    // so a corrupt slot cannot make this write anywhere else.
    if (const std::uint32_t off = atomic(w.asleep_on).exchange(0, std::memory_order_seq_cst);
        off != 0 && (off == t.sleeper_counts[0] || off == t.sleeper_counts[1]))
        atomic(*reinterpret_cast<std::uint32_t *>(t.base + off)).fetch_sub(1, std::memory_order_seq_cst);
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
inline void free_dead_waiters(const waiter_table &t) noexcept {
    for (std::uint16_t i = 0; i < t.count; ++i) {
        waiter_slot &w = t.slots[i];
        const std::uint32_t pid = atomic(w.owner_pid).load(std::memory_order_acquire);
        if (pid == 0)
            continue;
        const std::uint64_t start = atomic(w.owner_start).load(std::memory_order_acquire);
        const std::uint64_t pidns = atomic(w.owner_pidns).load(std::memory_order_acquire);
        // The slot changed hands between the reads: start and pidns may be the new owner's, not pid's.
        if (atomic(w.owner_pid).load(std::memory_order_acquire) != pid)
            continue;
        if (start == start_freeing || !owner_exited(pid, start, pidns))
            continue;
        free_dead_slot(t, w, pid, start);
    }
}

// Frees the slots this process claimed through t and still holds; a child created by fork inherits its
// parent's bits, and those slots stay the parent's.
inline void release_owned_slots(waiter_table &t) noexcept {
    for (std::uint16_t i = 0; i < t.count; ++i)
        if (owned_clear(t, i) && slot_is_mine(t, i))
            free_slot(t, i);
}

// Claims a free slot for this process, first freeing the slots of processes that have exited.
inline result<std::uint16_t> claim_slot(waiter_table &t) {
    const std::uint32_t pid = current_pid();
    const std::uint64_t start = current_start();
    const std::uint64_t pidns = current_pidns();
    free_dead_waiters(t);
    for (std::uint16_t i = 0; i < t.count; ++i) {
        waiter_slot &w = t.slots[i];
        std::uint32_t none = 0;
        if (!atomic(w.owner_pid)
                 .compare_exchange_strong(none, pid, std::memory_order_acq_rel, std::memory_order_relaxed))
            continue;
        // Counted before owner_start is stamped: a freer decrements only for a stamped slot, so a claimer
        // killed in between leaves the count too high, never too low.
        atomic(*t.claimed).fetch_add(1, std::memory_order_seq_cst);
        atomic(w.interrupt).store(0, std::memory_order_relaxed);
        atomic(w.owner_pidns).store(pidns, std::memory_order_relaxed);
        atomic(w.owner_start).store(start, std::memory_order_release);
        owned_set(t, i);
#ifdef _WIN32
        HANDLE e = event(t, i);
        if (e == nullptr) {
            const unexpected failed = os_failure();
            owned_clear(t, i);
            free_slot(t, i);
            return failed;
        }
        // Drops a wake-up meant for an earlier owner of the slot.
        ResetEvent(e);
#endif
        return i;
    }
    return unexpected(status::no_slot);
}

inline void release_slot(waiter_table &t, std::uint16_t slot) noexcept {
    if (slot < t.count && owned_clear(t, slot) && slot_is_mine(t, slot))
        free_slot(t, slot);
}

inline bool slot_held(const waiter_table &t, std::uint16_t slot) noexcept {
    return slot < t.count && owned_test(t, slot) && slot_is_mine(t, slot);
}

// Wakes every process waiting on word. The caller changes word first and decides whether anyone waits.
SHAREDBOX_HOT void wake_all(const waiter_table &t, std::uint32_t &word) noexcept {
#ifdef _WIN32
    static_cast<void>(word);
    for (std::uint16_t i = 0; i < t.count; ++i)
        if (atomic(t.slots[i].owner_pid).load(std::memory_order_seq_cst) != 0)
            if (HANDLE e = event(t, i); e != nullptr)
                SetEvent(e);
#else
    static_cast<void>(t);
    syscall(SYS_futex, &word, futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
}

// Blocks in a slot this process holds until changed() is true, the slot is interrupted, or timeout
// passes (status::timeout). changed must read with seq_cst: word is read before it, so a change between
// the two moves the word and the futex refuses to sleep. With sleepers, one of the counts in
// t.sleeper_counts, the wait counts itself there for its whole length, before its first check, so a
// writer that reads sleepers with seq_cst after its own change either sees the count or has its change
// seen by the check.
template <class Changed>
result<wake> wait_on(const waiter_table &t, std::uint16_t slot, std::uint32_t &word, Changed changed,
                     seconds timeout, std::uint32_t *sleepers = nullptr) {
    if (!slot_held(t, slot) || !timeout_ok(timeout, true))
        return unexpected(status::range);
    // The slot names the count only after the increment, and the exit subtracts only if it takes the name
    // back, so a freer of a dead owner's slot subtracts at most what was added: a kill between the two
    // steps leaves the count one too high, which costs wake calls and never loses a wake.
    struct counted {
        std::uint32_t *n;
        std::uint32_t &mark;
        counted(std::uint32_t *count, std::uint32_t &asleep_on, std::uint32_t offset) noexcept
            : n(count), mark(asleep_on) {
            if (n == nullptr)
                return;
            atomic(*n).fetch_add(1, std::memory_order_seq_cst);
            atomic(mark).store(offset, std::memory_order_seq_cst);
        }
        ~counted() {
            if (n != nullptr && atomic(mark).exchange(0, std::memory_order_seq_cst) != 0)
                atomic(*n).fetch_sub(1, std::memory_order_seq_cst);
        }
        counted(const counted &) = delete;
        counted &operator=(const counted &) = delete;
    } asleep(sleepers, t.slots[slot].asleep_on,
             sleepers == nullptr ? 0
                                 : static_cast<std::uint32_t>(reinterpret_cast<std::byte *>(sleepers) - t.base));
    const clock::time_point deadline = clock::now() + std::chrono::duration_cast<clock::duration>(timeout);
    for (;;) {
        const std::uint32_t seen = atomic(word).load(std::memory_order_seq_cst);
        if (changed())
            return wake::changed;
        std::uint32_t set = 1;
        if (atomic(t.slots[slot].interrupt)
                .compare_exchange_strong(set, 0, std::memory_order_acq_rel, std::memory_order_relaxed))
            return wake::interrupted;
        const clock::duration remaining = deadline - clock::now();
        if (remaining <= clock::duration::zero())
            return unexpected(status::timeout);
#ifdef _WIN32
        // Unused here, but the load of seen must stay: it orders changed() after a waker's change to word.
        static_cast<void>(seen);
        HANDLE e = event(t, slot);
        if (e == nullptr)
            return os_failure();
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
        if (WaitForSingleObject(e, static_cast<DWORD>(ms) + 1) == WAIT_FAILED)
            return os_failure();
#else
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(remaining).count();
        timespec ts{};
        ts.tv_sec = static_cast<time_t>(ns / 1000000000);
        ts.tv_nsec = static_cast<long>(ns % 1000000000);
        syscall(SYS_futex, &word, futex_wait, seen, &ts, nullptr, 0);
#endif
    }
}

// Ends the wait in slot, of any process, with wake::interrupted; word is the one that wait sleeps on.
// The flag stays set until that waiter sees it, as the box's interrupt documents.
inline result<void> interrupt_slot(const waiter_table &t, std::uint16_t slot, std::uint32_t &word) {
    if (slot >= t.count)
        return unexpected(status::range);
    atomic(t.slots[slot].interrupt).store(1, std::memory_order_seq_cst);
#ifdef _WIN32
    static_cast<void>(word);
    HANDLE e = event(t, slot);
    if (e == nullptr)
        return os_failure();
    SetEvent(e);
#else
    atomic(word).fetch_add(1, std::memory_order_seq_cst);
    syscall(SYS_futex, &word, futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
    return {};
}
} // namespace detail

// What create gives a segment's metadata: the document of version 1, none when initial is empty; the size
// in bytes of the ring that holds the documents, 0 for no metadata area; and how many versions the area
// keeps at once.
struct metadata_spec {
    std::span<const std::byte> initial;
    std::uint64_t capacity;
    std::uint32_t versions;
};

namespace detail {

inline constexpr std::uint32_t min_metadata_entries = 2;
inline constexpr std::uint32_t max_metadata_entries = 65535;
inline constexpr std::uint32_t max_metadata_depth = 32;

// Checks that a document is one CBOR item of the metadata subset with nothing after it.
class cbor_checker {
public:
    explicit cbor_checker(std::span<const std::byte> doc) noexcept
        : at_(doc.data()), end_(doc.data() + doc.size()) {}

    bool document() noexcept { return item(0, true) && at_ == end_; }

private:
    std::size_t left() const noexcept { return static_cast<std::size_t>(end_ - at_); }

    // Reads an initial byte and its argument. A reserved additional information and an indefinite length,
    // which a break also has, are refused here.
    bool head(std::uint8_t &major, std::uint8_t &info, std::uint64_t &argument) noexcept {
        if (at_ == end_)
            return false;
        const auto initial = static_cast<std::uint8_t>(*at_++);
        major = initial >> 5;
        info = initial & 0x1f;
        if (info < 24) {
            argument = info;
            return true;
        }
        if (info > 27)
            return false;
        const std::size_t n = std::size_t{1} << (info - 24);
        if (left() < n)
            return false;
        argument = 0;
        for (std::size_t i = 0; i < n; ++i)
            argument = argument << 8 | static_cast<std::uint8_t>(at_[i]);
        at_ += n;
        return true;
    }

    bool skip(std::uint64_t length, std::string_view &bytes) noexcept {
        if (length > left())
            return false;
        bytes = std::string_view(reinterpret_cast<const char *>(at_), static_cast<std::size_t>(length));
        at_ += length;
        return true;
    }

    bool text_key(bool check, std::string_view &key) noexcept {
        std::uint8_t major = 0;
        std::uint8_t info = 0;
        std::uint64_t length = 0;
        return head(major, info, length) && major == 3 && skip(length, key) && (!check || utf8_ok(key));
    }

    // Any strict order would do to find repeated keys; this one is the order of canonical encoders, so their
    // maps need no second pass.
    static bool key_before(std::string_view a, std::string_view b) noexcept {
        return a.size() < b.size() || (a.size() == b.size() && a < b);
    }

    // An item inside depth arrays and maps. Without check, an item already checked is only stepped over.
    bool item(std::uint32_t depth, bool check) noexcept {
        std::uint8_t major = 0;
        std::uint8_t info = 0;
        std::uint64_t argument = 0;
        if (!head(major, info, argument))
            return false;
        std::string_view bytes;
        switch (major) {
        case 0:
        case 1:
            return true;
        case 2:
            return skip(argument, bytes);
        case 3:
            return skip(argument, bytes) && (!check || utf8_ok(bytes));
        case 4:
            if (depth >= max_metadata_depth || argument > left())
                return false;
            for (std::uint64_t i = 0; i < argument; ++i)
                if (!item(depth + 1, check))
                    return false;
            return true;
        case 5:
            return depth < max_metadata_depth && map(argument, depth + 1, check);
        case 7:
            // false, true, null, and half, single and double floats.
            return (info >= 20 && info <= 22) || (info >= 25 && info <= 27);
        default:
            return false;
        }
    }

    bool map(std::uint64_t pairs, std::uint32_t depth, bool check) noexcept {
        if (pairs > left() / 2)
            return false;
        const std::byte *first = at_;
        bool sorted = true;
        std::string_view previous;
        for (std::uint64_t i = 0; i < pairs; ++i) {
            std::string_view key;
            if (!text_key(check, key))
                return false;
            if (i > 0 && !key_before(previous, key))
                sorted = false;
            previous = key;
            if (!item(depth, check))
                return false;
        }
        return !check || sorted || keys_unique(first, pairs, depth);
    }

    // Whether the pairs keys of the map starting at first, already checked, are all different. Refuses the
    // map when the memory to sort them cannot be had.
    bool keys_unique(const std::byte *first, std::uint64_t pairs, std::uint32_t depth) noexcept {
        const auto n = static_cast<std::size_t>(pairs);
        std::unique_ptr<std::string_view[]> keys(new (std::nothrow) std::string_view[n]);
        if (!keys)
            return false;
        const std::byte *after = at_;
        at_ = first;
        for (std::size_t i = 0; i < n; ++i) {
            static_cast<void>(text_key(false, keys[i]));
            static_cast<void>(item(depth, false));
        }
        at_ = after;
        std::sort(keys.get(), keys.get() + n, key_before);
        return std::adjacent_find(keys.get(), keys.get() + n) == keys.get() + n;
    }

    const std::byte *at_;
    const std::byte *end_;
};

} // namespace detail

// Whether doc is one value of the CBOR subset metadata documents use, with nothing after it: integers from
// -2**64 to 2**64 - 1, byte strings, UTF-8 text, arrays, maps whose keys are text strings each used once,
// false, true, null and floats. Every length is definite; tags, undefined, the other simple values and
// nesting deeper than 32 arrays and maps are refused.
[[nodiscard]] inline bool metadata_ok(std::span<const std::byte> doc) noexcept {
    return detail::cbor_checker(doc).document();
}

namespace detail {

// The first line of a metadata area, written once by the creator.
struct metadata_line {
    std::uint32_t entries;
    std::uint32_t reserved0;
    std::uint64_t ring_size;
    std::uint64_t reserved1[6];
};

// The entry of one version. seq is odd while the entry is written, 2 * version + 2 once it holds version
// whole, and 0 once an add has overwritten the version's bytes in the ring; offset and length place the
// version's document in the ring.
struct metadata_entry {
    std::uint64_t seq;
    std::uint64_t version;
    std::uint64_t position;
    std::uint32_t offset;
    std::uint32_t length;
};

static_assert(std::is_standard_layout_v<metadata_line> && std::is_trivially_copyable_v<metadata_line>);
static_assert(sizeof(metadata_line) == 64 && alignof(metadata_line) == 8);
static_assert(offsetof(metadata_line, entries) == 0 && offsetof(metadata_line, reserved0) == 4 &&
              offsetof(metadata_line, ring_size) == 8 && offsetof(metadata_line, reserved1) == 16);
static_assert(std::is_standard_layout_v<metadata_entry> && std::is_trivially_copyable_v<metadata_entry>);
static_assert(sizeof(metadata_entry) == 32 && alignof(metadata_entry) == 8);
static_assert(offsetof(metadata_entry, seq) == 0 && offsetof(metadata_entry, version) == 8 &&
              offsetof(metadata_entry, position) == 16 && offsetof(metadata_entry, offset) == 24 &&
              offsetof(metadata_entry, length) == 28);
static_assert(offsetof(metadata_entry, seq) % align64 == 0 && offsetof(metadata_entry, version) % align64 == 0 &&
              offsetof(metadata_entry, position) % align64 == 0);
static_assert(offsetof(metadata_entry, offset) % align32 == 0 && offsetof(metadata_entry, length) % align32 == 0);

// Versions are below this, so that 2 * version + 2, an entry's seq, does not wrap to 0.
inline constexpr std::uint64_t metadata_version_limit = UINT64_MAX >> 1;

// The bytes of an area of entries entries and a ring of ring_size bytes; UINT64_MAX when that overflows.
inline std::uint64_t metadata_area_size(std::uint32_t entries, std::uint64_t ring_size) noexcept {
    const std::uint64_t fixed = sizeof(metadata_line) + std::uint64_t{entries} * sizeof(metadata_entry);
    return ring_size > UINT64_MAX - fixed ? UINT64_MAX : fixed + ring_size;
}

// The longest document a ring of ring_size bytes takes: a third of it, rounded down to a multiple of 8.
// Since every document starts at a multiple of 8, a document that wraps to 0 could otherwise reach the
// start of the newest one.
inline std::uint64_t max_metadata_document(std::uint64_t ring_size) noexcept { return ring_size / 3 / 8 * 8; }

// Whether a line's geometry can be used at all: a ring below 24 bytes takes no document, and an area that
// fits a u32 size keeps every ring offset a u32.
inline bool metadata_line_ok(const metadata_line &line) noexcept {
    return line.entries >= min_metadata_entries && line.entries <= max_metadata_entries && line.ring_size >= 24 &&
           line.ring_size % 8 == 0 && metadata_area_size(line.entries, line.ring_size) <= UINT32_MAX;
}

// Whether a copy of an area's first line describes an area that fits in metadata_size bytes.
inline bool metadata_geometry_ok(const metadata_line &line, std::uint32_t metadata_size) noexcept {
    return metadata_line_ok(line) && metadata_area_size(line.entries, line.ring_size) <= metadata_size;
}

// Writes the first line and empty entries of an area of metadata_area_size(entries, ring_size) bytes;
// status::range for a geometry metadata_geometry_ok would refuse.
inline status init_metadata(std::byte *area, std::uint32_t entries, std::uint64_t ring_size) noexcept {
    metadata_line line{};
    line.entries = entries;
    line.ring_size = ring_size;
    if (!metadata_line_ok(line))
        return status::range;
    std::memcpy(area, &line, sizeof line);
    std::memset(area + sizeof line, 0, std::size_t{entries} * sizeof(metadata_entry));
    return status::ok;
}

// The ring and area sizes create lays out for a metadata_spec, both 0 for no area.
struct metadata_plan {
    std::uint64_t ring_size;
    std::uint64_t area_size;
};

// Checks spec and sizes its area: status::range for a capacity above what an area of u32 size holds, a
// version count outside 2 to 65535, a ring below 24 bytes, an initial document that is longer than the
// ring takes or outside the subset, or an initial document with capacity 0.
inline result<metadata_plan> plan_metadata(const metadata_spec &spec) noexcept {
    if (spec.capacity == 0)
        return spec.initial.empty() ? result<metadata_plan>(metadata_plan{0, 0})
                                    : result<metadata_plan>(unexpected(status::range));
    if (spec.capacity > UINT32_MAX || spec.versions < min_metadata_entries || spec.versions > max_metadata_entries)
        return unexpected(status::range);
    const std::uint64_t ring = round_up(spec.capacity, 8);
    const std::uint64_t area = metadata_area_size(spec.versions, ring);
    if (ring < 24 || area > UINT32_MAX || spec.initial.size() > max_metadata_document(ring) ||
        (!spec.initial.empty() && !metadata_ok(spec.initial)))
        return unexpected(status::range);
    return metadata_plan{ring, area};
}

// A copy of an area's first line, made once when the segment is opened; only the copy is checked and used.
inline metadata_line copy_metadata_line(const std::byte *area) noexcept {
    metadata_line line{};
    std::memcpy(&line, area, sizeof line);
    return line;
}

// The entry version lives in. Not const, because std::atomic_ref, which every access to an entry goes
// through, takes no const object; readers only load.
inline metadata_entry &metadata_entry_of(const std::byte *area, const metadata_line &line,
                                         std::uint64_t version) noexcept {
    auto *table = reinterpret_cast<metadata_entry *>(const_cast<std::byte *>(area) + sizeof(metadata_line));
    return table[version % line.entries];
}

// Loads entry e's seq with acquire, then its fields into out; false when e does not hold version, and for
// a version no add makes.
inline bool load_metadata_entry(metadata_entry &e, std::uint64_t version, metadata_entry &out) noexcept {
    if (version >= metadata_version_limit)
        return false;
    out.seq = atomic(e.seq).load(std::memory_order_acquire);
    if (out.seq != 2 * version + 2)
        return false;
    out.version = atomic(e.version).load(std::memory_order_relaxed);
    out.position = atomic(e.position).load(std::memory_order_relaxed);
    out.offset = atomic(e.offset).load(std::memory_order_relaxed);
    out.length = atomic(e.length).load(std::memory_order_relaxed);
    return out.version == version;
}

// Whether e still holds what its seq named when load_metadata_entry read it, after a copy of its fields and
// of the bytes they place, which the fence keeps before the second load.
inline bool metadata_entry_kept(metadata_entry &e, std::uint64_t seq) noexcept {
    std::atomic_thread_fence(std::memory_order_acquire);
    return atomic(e.seq).load(std::memory_order_relaxed) == seq;
}

// The length of version's document: status::not_found when the area does not hold version, and
// status::corrupt when its entry places the document outside the ring. line is the copy of the area's
// first line that metadata_geometry_ok accepted, here and in every function below.
inline result<std::uint32_t> metadata_length(const std::byte *area, const metadata_line &line,
                                             std::uint64_t version) noexcept {
    if (version == 0)
        return unexpected(status::not_found);
    metadata_entry &e = metadata_entry_of(area, line, version);
    metadata_entry copy{};
    if (!load_metadata_entry(e, version, copy))
        return unexpected(status::not_found);
    const bool inside = std::uint64_t{copy.offset} + copy.length <= line.ring_size;
    if (!metadata_entry_kept(e, copy.seq))
        return unexpected(status::not_found);
    if (!inside)
        return unexpected(status::corrupt);
    return copy.length;
}

// Copies version's document into out and returns its length. As metadata_length, and status::range for an
// out shorter than the document; status::not_found also when the version is overwritten during the copy.
inline result<std::uint32_t> read_metadata(const std::byte *area, const metadata_line &line, std::uint64_t version,
                                           std::span<std::byte> out) noexcept {
    if (version == 0)
        return unexpected(status::not_found);
    metadata_entry &e = metadata_entry_of(area, line, version);
    metadata_entry copy{};
    if (!load_metadata_entry(e, version, copy))
        return unexpected(status::not_found);
    // offset and length may come from an add that reuses the entry, so a refusal stands only once seq shows
    // the entry unchanged.
    if (std::uint64_t{copy.offset} + copy.length > line.ring_size)
        return unexpected(metadata_entry_kept(e, copy.seq) ? status::corrupt : status::not_found);
    if (copy.length > out.size())
        return unexpected(metadata_entry_kept(e, copy.seq) ? status::range : status::not_found);
    const std::byte *ring = area + sizeof(metadata_line) + std::uint64_t{line.entries} * sizeof(metadata_entry);
    if (copy.length != 0)
        std::memcpy(out.data(), ring + copy.offset, copy.length);
    if (!metadata_entry_kept(e, copy.seq))
        return unexpected(status::not_found);
    return copy.length;
}

// The newest version at or before head whose position is at most position, found by walking back at most
// one step per entry. status::not_found when position comes before every version still held, or when a
// version the walk passes has been overwritten.
inline result<std::uint64_t> metadata_version_at(const std::byte *area, const metadata_line &line,
                                                 std::uint64_t head, std::uint64_t position) noexcept {
    std::uint64_t steps = 0;
    for (std::uint64_t v = head; v != 0 && steps < line.entries; --v, ++steps) {
        metadata_entry &e = metadata_entry_of(area, line, v);
        metadata_entry copy{};
        if (!load_metadata_entry(e, v, copy) || !metadata_entry_kept(e, copy.seq))
            return unexpected(status::not_found);
        if (copy.position <= position)
            return v;
    }
    return unexpected(status::not_found);
}

// Adds doc as version head + 1, taking effect at position, stores it in head and returns it. Only one
// process at a time adds to an area. The add writes over the entry of an older version, and over the
// bytes of older versions its document covers; may_reclaim, called for each such version u with the
// position of version u + 1, before which every item under u lies, says whether u may go, and the add
// gives status::metadata_full when one may not. A document outside the subset or longer than
// max_metadata_document gives status::range. Both refusals write nothing.
template <class F>
    requires std::is_invocable_r_v<bool, F &, std::uint64_t>
result<std::uint64_t> add_metadata(std::byte *area, const metadata_line &line, std::atomic_ref<std::uint64_t> head,
                                   std::span<const std::byte> doc, std::uint64_t position, F &&may_reclaim) {
    if (doc.size() > max_metadata_document(line.ring_size) || !metadata_ok(doc))
        return unexpected(status::range);
    const std::uint64_t newest = head.load(std::memory_order_acquire);
    // Only a forged head reaches this; past it the walk below would cover almost every u64.
    if (newest >= metadata_version_limit - 1)
        return unexpected(status::corrupt);
    const std::uint64_t v = newest + 1;
    const std::uint64_t size = doc.size();
    std::uint64_t at = 0;
    if (newest != 0) {
        metadata_entry newest_entry{};
        if (!load_metadata_entry(metadata_entry_of(area, line, newest), newest, newest_entry) ||
            std::uint64_t{newest_entry.offset} + newest_entry.length > line.ring_size)
            return unexpected(status::corrupt);
        const std::uint64_t end = round_up(std::uint64_t{newest_entry.offset} + newest_entry.length, 8);
        if (end + size <= line.ring_size)
            at = end;
    }
    // The version whose entry v takes, 0 for none; the entries of all versions before it are taken already.
    const std::uint64_t reused = v > line.entries ? v - line.entries : 0;
    const std::uint64_t oldest = reused == 0 ? 1 : reused;
    const auto overwritten = [&](std::uint64_t u, const metadata_entry &e) noexcept {
        return u == reused || (at < std::uint64_t{e.offset} + e.length && e.offset < at + size);
    };
    for (std::uint64_t u = oldest; u < newest; ++u) {
        metadata_entry e{};
        if (!load_metadata_entry(metadata_entry_of(area, line, u), u, e) || !overwritten(u, e))
            continue;
        // u + 1 is at most newest, so its entry is not reused yet and still holds its position, even after an
        // earlier add overwrote its bytes and cleared its seq.
        metadata_entry &next = metadata_entry_of(area, line, u + 1);
        if (!may_reclaim(atomic(next.position).load(std::memory_order_relaxed)))
            return unexpected(status::metadata_full);
    }
    for (std::uint64_t u = oldest; u < newest; ++u) {
        metadata_entry &shared = metadata_entry_of(area, line, u);
        metadata_entry e{};
        if (u != reused && load_metadata_entry(shared, u, e) && overwritten(u, e))
            atomic(shared.seq).store(0, std::memory_order_relaxed);
    }
    metadata_entry &slot = metadata_entry_of(area, line, v);
    atomic(slot.seq).store(2 * v + 1, std::memory_order_relaxed);
    // Keeps the stores that follow from moving before the seq stores above, so a reader whose copy sees
    // any of them sees its entry's seq changed too.
    std::atomic_thread_fence(std::memory_order_release);
    std::byte *ring = area + sizeof(metadata_line) + std::uint64_t{line.entries} * sizeof(metadata_entry);
    std::memcpy(ring + at, doc.data(), size);
    atomic(slot.version).store(v, std::memory_order_relaxed);
    atomic(slot.position).store(position, std::memory_order_relaxed);
    atomic(slot.offset).store(static_cast<std::uint32_t>(at), std::memory_order_relaxed);
    atomic(slot.length).store(static_cast<std::uint32_t>(size), std::memory_order_relaxed);
    atomic(slot.seq).store(2 * v + 2, std::memory_order_release);
    head.store(v, std::memory_order_release);
    return v;
}

} // namespace detail

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

// A copy of line 0 of the segment called name, of any kind, read without waiting and without the checks
// of open: not_found when nothing under that name has published a header.
[[nodiscard]] inline result<common_header> inspect(std::string_view name) noexcept {
    if (!detail::name_ok(name))
        return unexpected(status::range);
    detail::backoff no_wait(0);
    result<detail::os_mapping> map = detail::map_open(name, no_wait);
    if (!map)
        return unexpected(map.error());
    auto *line0 = static_cast<common_header *>(map->base());
    if (detail::atomic(line0->magic).load(std::memory_order_acquire) == 0)
        return unexpected(status::not_found);
    return detail::copy_common(line0);
}

class stream;

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
        if (!described() || (ref_.kind != kind_array && ref_.kind != kind_max_array))
            return {};
        return {&table_->numbers_view(node().numbers), node().ndim};
    }
    // The description's bytes, as the segment stores them; empty for a kind without one.
    std::span<const std::byte> description() const noexcept {
        return described() ? table_->bytes().subspan(node().at, node().length) : std::span<const std::byte>{};
    }

private:
    friend class handle;
    friend class stream;
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

// Rounds to nearest even straight from the double, since going through float would round twice.
[[nodiscard]] inline status encode_float16(double value, std::span<std::byte> out) noexcept {
    if (out.size() != 2)
        return status::range;
    std::uint64_t bits;
    std::memcpy(&bits, &value, sizeof bits);
    const auto sign = static_cast<std::uint16_t>((bits >> 48) & 0x8000u);
    const double magnitude = std::fabs(value);
    std::uint16_t half;
    if (std::isnan(value))
        half = static_cast<std::uint16_t>(sign | 0x7E00u | ((bits >> 42) & 0x3FFu));
    else if (std::isinf(value))
        half = static_cast<std::uint16_t>(sign | 0x7C00u);
    else if (magnitude >= 65520.0)
        return status::range;
    else if (magnitude < 0x1p-14)
        half = static_cast<std::uint16_t>(sign | static_cast<std::uint16_t>(std::nearbyint(magnitude * 0x1p24)));
    else {
        int exponent = 0;
        static_cast<void>(std::frexp(magnitude, &exponent));
        // magnitude is m * 2^(exponent - 1) with m in [1, 2); 1024 to 2048 after scaling, and 2048 carries
        // into the exponent.
        const auto scaled = static_cast<std::uint32_t>(std::nearbyint(std::ldexp(magnitude, 11 - exponent)));
        half =
            static_cast<std::uint16_t>(sign | ((static_cast<std::uint32_t>(exponent + 14) << 10) + scaled - 1024));
    }
    detail::put(out, 0, half);
    return status::ok;
}

[[nodiscard]] inline result<double> decode_float16(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 2)
        return unexpected(status::range);
    const auto half = detail::load<std::uint16_t>(bytes, 0);
    const std::uint32_t exponent = (half >> 10) & 0x1Fu;
    const std::uint32_t mantissa = half & 0x3FFu;
    double magnitude;
    if (exponent == 0x1F && mantissa != 0) {
        const std::uint64_t bits =
            (std::uint64_t{half & 0x8000u} << 48) | 0x7FF8000000000000u | (std::uint64_t{mantissa} << 42);
        double nan;
        std::memcpy(&nan, &bits, sizeof nan);
        return nan;
    }
    if (exponent == 0x1F)
        magnitude = std::numeric_limits<double>::infinity();
    else if (exponent == 0)
        magnitude = std::ldexp(static_cast<double>(mantissa), -24);
    else
        magnitude = std::ldexp(static_cast<double>(mantissa + 1024), static_cast<int>(exponent) - 25);
    return (half & 0x8000u) != 0 ? -magnitude : magnitude;
}

[[nodiscard]] inline status encode_float32(double value, std::span<std::byte> out) noexcept {
    if (out.size() != 4 || (std::isfinite(value) && std::fabs(value) >= 0x1.ffffffp127))
        return status::range;
    detail::put(out, 0, static_cast<float>(value));
    return status::ok;
}

[[nodiscard]] inline result<double> decode_float32(std::span<const std::byte> bytes) noexcept {
    if (bytes.size() != 4)
        return unexpected(status::range);
    return static_cast<double>(detail::load<float>(bytes, 0));
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

} // namespace v3
} // namespace sharedbox

#endif
