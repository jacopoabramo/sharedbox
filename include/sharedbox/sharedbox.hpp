// sharedbox.hpp: layout 1.0 of a sharedbox segment and the protocols that use it.
//
// C++20, header-only, 64-bit little-endian targets, no exceptions. Names in sharedbox::detail are
// internal and may change in any release. Every translation unit of a program must see the same
// definition of sharedbox::result, so build them all as C++20 or all as C++23.
#ifndef SHAREDBOX_SHAREDBOX_HPP
#define SHAREDBOX_SHAREDBOX_HPP

#include <sharedbox/sharedbox_c.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
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
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
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

inline constexpr std::uint16_t layout_major = 1;
inline constexpr std::uint16_t layout_minor = 0;
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

inline constexpr std::uint8_t kind_bool = 0;
inline constexpr std::uint8_t kind_int = 1;
inline constexpr std::uint8_t kind_float = 2;
inline constexpr std::uint8_t kind_str = 3;
inline constexpr std::uint8_t kind_bytes = 4;

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
};

// A field as create takes it and field() returns it; kind is one of the kind_ constants.
struct field_spec {
    std::uint32_t offset;
    std::uint32_t capacity;
    std::uint8_t kind;
};

// Bytes for one field in the record encoding, without the length prefix of str and bytes.
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
    std::uint8_t reserved0[4];
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

static_assert(std::is_standard_layout_v<header> && std::is_trivially_copyable_v<header>);
static_assert(std::is_standard_layout_v<stored_field> && std::is_trivially_copyable_v<stored_field>);
static_assert(std::is_standard_layout_v<waiter_slot> && std::is_trivially_copyable_v<waiter_slot>);
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
static_assert(offsetof(header, reserved0) == 60);
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
inline constexpr std::size_t align32 = std::atomic_ref<std::uint32_t>::required_alignment;
inline constexpr std::size_t align64 = std::atomic_ref<std::uint64_t>::required_alignment;
static_assert(offsetof(header, magic) % align64 == 0 && offsetof(header, seq) % align64 == 0);
static_assert(offsetof(header, writer_pid) % align32 == 0 && offsetof(header, wake_word) % align32 == 0);
static_assert(offsetof(header, waiters) % align32 == 0);
static_assert(offsetof(waiter_slot, owner_start) % align64 == 0 &&
              offsetof(waiter_slot, owner_pidns) % align64 == 0);
static_assert(offsetof(waiter_slot, owner_pid) % align32 == 0 && offsetof(waiter_slot, interrupt) % align32 == 0);
// The write counts start at header_size + field_count * sizeof(stored_field), the slots after them.
static_assert(header_size % align64 == 0 && sizeof(stored_field) % align64 == 0);
static_assert(sizeof(waiter_slot) % align64 == 0 && page_size % alignof(header) == 0);

static_assert(SBX_OK == int(status::ok) && SBX_E_EXISTS == int(status::exists));
static_assert(SBX_E_NOT_FOUND == int(status::not_found) && SBX_E_LAYOUT == int(status::layout));
static_assert(SBX_E_SCHEMA == int(status::schema) && SBX_E_CORRUPT == int(status::corrupt));
static_assert(SBX_E_LOCK_TIMEOUT == int(status::lock_timeout) && SBX_E_TIMEOUT == int(status::timeout));
static_assert(SBX_E_NO_SLOT == int(status::no_slot) && SBX_E_RANGE == int(status::range));
static_assert(SBX_E_OS == int(status::os));

#if defined(__cpp_lib_expected) && __cpp_lib_expected >= 202211L

template <class T> using result = std::expected<T, status>;
using unexpected = std::unexpected<status>;

#else

// The error of a result, as std::unexpected<status> is on C++23.
class unexpected {
public:
    constexpr explicit unexpected(status e) noexcept : error_(e) {}
    constexpr status error() const noexcept { return error_; }

private:
    status error_;
};

template <class T> class result;
template <> class result<void>;

namespace detail {
template <class> inline constexpr bool is_result = false;
template <class T> inline constexpr bool is_result<result<T>> = true;
} // namespace detail

// A value or an error status, with the part of the interface of std::expected<T, status> this library
// uses. value() on an error terminates, where std::expected would throw.
template <class T> class result {
public:
    using value_type = T;
    using error_type = status;

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
    constexpr status error() const noexcept { return std::get_if<1>(&v_)->error(); }
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
        static_assert(std::is_same_v<std::remove_cvref_t<std::invoke_result_t<F, status>>, result>,
                      "sharedbox::result: or_else must return the same result type");
        return has_value() ? result(**this) : std::forward<F>(f)(error());
    }
    template <class F> constexpr result or_else(F &&f) && {
        static_assert(std::is_same_v<std::remove_cvref_t<std::invoke_result_t<F, status>>, result>,
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
    using error_type = status;

    constexpr result() noexcept = default;
    constexpr result(unexpected e) noexcept : has_value_(false), error_(e.error()) {}

    constexpr bool has_value() const noexcept { return has_value_; }
    constexpr explicit operator bool() const noexcept { return has_value(); }
    constexpr void operator*() const noexcept {}
    constexpr void value() const {
        if (!has_value())
            std::terminate();
    }
    constexpr status error() const noexcept { return error_; }
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
        static_assert(std::is_same_v<std::remove_cvref_t<std::invoke_result_t<F, status>>, result>,
                      "sharedbox::result: or_else must return the same result type");
        return has_value() ? *this : std::forward<F>(f)(error_);
    }

private:
    bool has_value_ = true;
    status error_ = status::ok;
};

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

} // namespace detail

#ifndef _WIN32
// Runs the cache's first initialisation at load time, while only one thread exists. Without this, a
// fork while another thread is still inside that initialisation would leave the child stopped on the
// same compiler-generated guard, which only the (now absent) initialising thread would ever clear.
inline const bool process_cache_ready = (detail::cache(), true);
#endif

// What process_start returns for a process that exists but cannot be inspected.
inline constexpr std::uint64_t start_unknown = UINT64_MAX;

// This process's pid. On Linux it is read once and again in every child created by fork.
inline std::uint32_t current_pid() noexcept {
#ifdef _WIN32
    return static_cast<std::uint32_t>(GetCurrentProcessId());
#else
    if (!detail::atfork_registered.load(std::memory_order_relaxed))
        return static_cast<std::uint32_t>(getpid());
    detail::process_cache &c = detail::cache();
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
    if (!detail::atfork_registered.load(std::memory_order_relaxed))
        return process_start(current_pid());
#endif
    detail::process_cache &c = detail::cache();
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
    if (!detail::atfork_registered.load(std::memory_order_relaxed))
        return detail::read_pidns();
    detail::process_cache &c = detail::cache();
    std::uint64_t cached = c.pidns.load(std::memory_order_relaxed);
    if (cached == 0) {
        cached = detail::read_pidns() + 1;
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

namespace detail {

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
            sleep_ = std::min(sleep_ * 2, std::chrono::microseconds(1000));
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

inline bool prefixed(std::uint32_t kind) noexcept { return kind == kind_str || kind == kind_bytes; }

// Alignment of a field of this kind within the record: 1, 8, 8, 4, 4.
inline std::uint32_t kind_alignment(std::uint32_t kind) noexcept {
    return kind == kind_bool ? 1u : prefixed(kind) ? 4u : 8u;
}

// Bytes the field occupies in the record, the length prefix of str and bytes included.
inline std::uint64_t field_span(const field_spec &f) noexcept {
    return (prefixed(f.kind) ? 4u : 0u) + std::uint64_t{f.capacity};
}

inline bool field_fits(const field_spec &f, std::uint32_t record_size) noexcept {
    if (f.kind > kind_bytes || f.capacity == 0 || f.capacity > max_capacity)
        return false;
    if (f.offset % kind_alignment(f.kind) != 0 || f.offset > record_size)
        return false;
    if (!prefixed(f.kind) && f.capacity != (f.kind == kind_bool ? 1u : 8u))
        return false;
    return field_span(f) <= std::uint64_t{record_size} - f.offset;
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

// Keeps the OS error of a failed call across the cleanup that follows it.
class keep_os_error {
public:
#ifdef _WIN32
    keep_os_error() noexcept : error_(GetLastError()) {}
    ~keep_os_error() { SetLastError(error_); }

private:
    DWORD error_;
#else
    keep_os_error() noexcept : error_(errno) {}
    ~keep_os_error() { errno = error_; }

private:
    int error_;
#endif
};

// One view of a named mapping and the OS handle it came from; the destructor unmaps and closes both,
// keeping the OS error of whatever failed before.
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
        keep_os_error keep;
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
        return unexpected(GetLastError() == ERROR_INVALID_HANDLE ? status::exists : status::os);
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mapping);
        return unexpected(status::exists);
    }
    void *view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    if (view == nullptr) {
        keep_os_error keep;
        CloseHandle(mapping);
        return unexpected(status::os);
    }
    return os_mapping::adopt(mapping, view, size);
#else
    const object_name path = make_name("/", name, "");
    const int fd = shm_open(path.data(), O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0)
        return unexpected(errno == EEXIST ? status::exists : status::os);
    if (allocate(fd, size) == 0) {
        void *view = mmap(nullptr, static_cast<std::size_t>(size), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (view != MAP_FAILED)
            return os_mapping::adopt(fd, view, size);
    }
    keep_os_error keep;
    close(fd);
    shm_unlink(path.data());
    return unexpected(status::os);
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
        return unexpected(e == ERROR_FILE_NOT_FOUND || e == ERROR_INVALID_HANDLE ? status::not_found : status::os);
    }
    void *view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    MEMORY_BASIC_INFORMATION info;
    if (view == nullptr || VirtualQuery(view, &info, sizeof info) == 0) {
        keep_os_error keep;
        if (view != nullptr)
            UnmapViewOfFile(view);
        CloseHandle(mapping);
        return unexpected(status::os);
    }
    return os_mapping::adopt(mapping, view, static_cast<std::uint64_t>(info.RegionSize));
#else
    const object_name path = make_name("/", name, "");
    const int fd = shm_open(path.data(), O_RDWR, 0);
    if (fd < 0)
        return unexpected(errno == ENOENT ? status::not_found : status::os);
    struct stat st;
    for (;;) {
        if (fstat(fd, &st) != 0) {
            keep_os_error keep;
            close(fd);
            return unexpected(status::os);
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
        keep_os_error keep;
        close(fd);
        return unexpected(status::os);
    }
    return os_mapping::adopt(fd, view, static_cast<std::uint64_t>(st.st_size));
#endif
}

#ifndef _WIN32
// Fills buf from getrandom, or from /dev/urandom where the kernel is older than 3.17 or a seccomp
// profile refuses getrandom.
inline bool fill_random(unsigned char *buf, std::size_t size) noexcept {
    std::size_t got = 0;
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
        break;
    }
    if (fd >= 0) {
        keep_os_error keep;
        close(fd);
    }
    return got == size;
}
#endif

// 8 bytes from the OS random source, never 0.
inline result<std::uint64_t> random_id() noexcept {
    std::uint64_t id = 0;
    while (id == 0) {
#ifdef _WIN32
        const NTSTATUS rc =
            BCryptGenRandom(nullptr, reinterpret_cast<PUCHAR>(&id), sizeof id, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
        if (!BCRYPT_SUCCESS(rc))
            return unexpected(status::os);
#else
        if (!fill_random(reinterpret_cast<unsigned char *>(&id), sizeof id))
            return unexpected(status::os);
#endif
    }
    return id;
}

// Checks line 0 of a header, copied out of a mapping of mapped_size bytes, before anything reads
// through it.
inline status check_geometry(const header &h, std::uint64_t mapped_size) noexcept {
    if (h.field_count == 0 || h.field_count > max_fields)
        return status::corrupt;
    if (h.waiter_slots == 0 || h.waiter_slots > max_waiter_slots)
        return status::corrupt;
    if (h.tail != header_size || h.size != mapped_size || h.record % record_alignment != 0)
        return status::corrupt;
    if (h.record < tail_end(h.field_count, h.waiter_slots))
        return status::corrupt;
    if (h.record_size > max_record_size || std::uint64_t{h.record} + h.record_size > mapped_size)
        return status::corrupt;
    return status::ok;
}

// Line 0 of the header at base, copied once so that only the copy is checked and used.
inline header copy_line0(const void *base) noexcept {
    header line0{};
    std::memcpy(&line0, base, 64);
    return line0;
}

struct state;

} // namespace detail

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
    // the initial values; no other process can open it before all of that is written.
    [[nodiscard]] static result<handle> create(std::string_view name, std::span<const field_spec> fields,
                                               std::uint32_t record_size, std::uint64_t schema_hash,
                                               std::uint16_t waiter_slots, std::span<const value> initial);
    // Opens the box called name, waiting up to timeout for a creator that has not finished. The caller
    // compares schema_hash() with its own.
    [[nodiscard]] static result<handle> open(std::string_view name, seconds timeout);

    std::string_view name() const noexcept;
    std::uint16_t field_count() const noexcept;
    // The field as checked when the handle was made; later changes to the shared table are ignored.
    // index must be below field_count().
    const field_spec &field(std::uint16_t index) const noexcept;
    std::uint32_t record_size() const noexcept;
    std::uint64_t schema_hash() const noexcept;
    // Random at creation, so a box made again under the same name has another.
    std::uint64_t create_id() const noexcept;
    std::uint16_t waiter_slots() const noexcept;
    // The segment's minor version, or this header's if that is lower.
    std::uint16_t minor_version() const noexcept;
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
    // The stored bytes of field inside a copy made by read_record.
    std::span<const std::byte> payload(std::uint16_t field, std::span<const std::byte> record) const noexcept;
    // Writes every value under one lock, so readers see all of them or none, then wakes waiters.
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

private:
    explicit handle(detail::state *s) noexcept : s_(s) {}
    void swap(handle &other) noexcept { std::swap(s_, other.s_); }

    detail::state *s_ = nullptr;
};

namespace detail {

// What a handle keeps: the checked copy of line 0 and the field table, pointers into the mapping, and
// the per-process bookkeeping.
struct state {
    char name[name_max + 1] = {};
    std::uint16_t field_count = 0;
    std::uint16_t waiter_slots = 0;
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
    os_mapping map;
    double lock_timeout = default_lock_timeout;
    void *(*before_wait)() = nullptr;
    void (*after_wait)(void *) = nullptr;
    std::array<std::atomic<std::uint64_t>, max_waiter_slots / 64> owned{};
#ifdef _WIN32
    std::unique_ptr<std::atomic<HANDLE>[]> events;
#endif

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
inline void bind(state &s, void *base, std::uint64_t size, std::uint16_t segment_minor) noexcept {
    auto *bytes = static_cast<std::byte *>(base);
    const std::size_t table = header_size + std::size_t{s.field_count} * sizeof(stored_field);
    s.hdr = static_cast<header *>(base);
    s.counts = reinterpret_cast<std::uint64_t *>(bytes + table);
    s.slots = reinterpret_cast<waiter_slot *>(bytes + table + std::size_t{s.field_count} * sizeof(std::uint64_t));
    s.record = bytes + s.record_offset;
    s.size = size;
    s.layout_minor = segment_minor < sharedbox::layout_minor ? segment_minor : sharedbox::layout_minor;
}

// Copies and checks the field table of a mapping whose line 0 passed check_geometry.
inline status copy_fields(state &s, const void *base) noexcept {
    for (std::uint16_t i = 0; i < s.field_count; ++i) {
        stored_field stored;
        std::memcpy(&stored, static_cast<const std::byte *>(base) + header_size + std::size_t{i} * sizeof stored,
                    sizeof stored);
        const field_spec f{stored.offset, stored.capacity_and_kind & capacity_mask,
                           static_cast<std::uint8_t>(stored.capacity_and_kind >> kind_shift)};
        if (!field_fits(f, s.record_size))
            return status::corrupt;
        s.fields[i] = f;
    }
    return fields_overlap({s.fields.get(), s.field_count}) ? status::corrupt : status::ok;
}

inline bool values_ok(std::span<const field_spec> fields, std::span<const value> values) noexcept {
    for (const value &v : values) {
        if (v.field >= fields.size())
            return false;
        const field_spec &f = fields[v.field];
        if (prefixed(f.kind) ? v.bytes.size() > f.capacity : v.bytes.size() != f.capacity)
            return false;
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

} // namespace detail

inline handle::~handle() { delete s_; }

inline result<handle> handle::create(std::string_view name, std::span<const field_spec> fields,
                                     std::uint32_t record_size, std::uint64_t schema_hash,
                                     std::uint16_t waiter_slots, std::span<const value> initial) {
    if (!detail::name_ok(name) || fields.empty() || fields.size() > max_fields || waiter_slots == 0 ||
        waiter_slots > max_waiter_slots || record_size > detail::max_record_size)
        return unexpected(status::range);
    for (const field_spec &f : fields)
        if (!detail::field_fits(f, record_size))
            return unexpected(status::range);
    if (detail::fields_overlap(fields) || !detail::values_ok(fields, initial))
        return unexpected(status::range);
    const auto count = static_cast<std::uint16_t>(fields.size());
    const auto record =
        static_cast<std::uint32_t>(detail::round_up(detail::tail_end(count, waiter_slots), record_alignment));
    const std::uint64_t size = detail::round_up(std::uint64_t{record} + record_size, page_size);
    const result<std::uint64_t> id = detail::random_id();
    if (!id)
        return unexpected(id.error());
    std::unique_ptr<detail::state> s = detail::make_state(name, count, waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    result<detail::os_mapping> map = detail::map_create(name, size);
    if (!map)
        return unexpected(map.error());
    s->map = std::move(*map);
    auto *base = s->map.base();
    header &h = *static_cast<header *>(base);
    h.layout_major = layout_major;
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
        const stored_field stored{fields[i].offset,
                                  fields[i].capacity | std::uint32_t{fields[i].kind} << kind_shift};
        std::memcpy(static_cast<std::byte *>(base) + header_size + std::size_t{i} * sizeof stored, &stored,
                    sizeof stored);
        s->fields[i] = fields[i];
    }
    s->record_size = record_size;
    s->record_offset = record;
    s->schema_hash = schema_hash;
    s->create_id = *id;
    detail::bind(*s, base, size, layout_minor);
    for (const value &v : initial)
        detail::store(*s, v);
    detail::atomic(h.magic).store(magic, std::memory_order_release);
    return handle(s.release());
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
    if (line0.layout_major != layout_major)
        return unexpected(status::layout);
    if (const status rc = detail::check_geometry(line0, map->size()); rc != status::ok)
        return unexpected(rc);
    std::unique_ptr<detail::state> s = detail::make_state(name, line0.field_count, line0.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    s->record_size = line0.record_size;
    if (const status rc = detail::copy_fields(*s, hdr); rc != status::ok)
        return unexpected(rc);
    s->record_offset = line0.record;
    s->schema_hash = line0.schema_hash;
    s->create_id = line0.create_id;
    s->map = std::move(*map);
    detail::bind(*s, s->map.base(), s->map.size(), line0.layout_minor);
    detail::free_dead_waiters(*s);
    return handle(s.release());
}

inline std::string_view handle::name() const noexcept { return s_->name; }
inline std::uint16_t handle::field_count() const noexcept { return s_->field_count; }
inline const field_spec &handle::field(std::uint16_t index) const noexcept { return s_->fields[index]; }
inline std::uint32_t handle::record_size() const noexcept { return s_->record_size; }
inline std::uint64_t handle::schema_hash() const noexcept { return s_->schema_hash; }
inline std::uint64_t handle::create_id() const noexcept { return s_->create_id; }
inline std::uint16_t handle::waiter_slots() const noexcept { return s_->waiter_slots; }
inline std::uint16_t handle::minor_version() const noexcept { return s_->layout_minor; }
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
        return unexpected(errno == ENOENT ? status::not_found : status::os);
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

// Runs attempt until it succeeds, pausing between tries; false once timeout seconds have passed. The
// hooks run only when the first try fails, before the first pause and after the last.
template <class F> SHAREDBOX_HOT bool retry(const state &s, double timeout, F &&attempt) {
    if (attempt())
        return true;
    void *hook = s.before_wait != nullptr ? s.before_wait() : nullptr;
    backoff wait(timeout);
    bool done = false;
    while (!wait.expired()) {
        wait.pause();
        if (attempt()) {
            done = true;
            break;
        }
    }
    if (s.after_wait != nullptr)
        s.after_wait(hook);
    return done;
}

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

inline std::span<const std::byte> handle::payload(std::uint16_t field,
                                                  std::span<const std::byte> record) const noexcept {
    return detail::payload(s_->fields[field], record.data());
}

SHAREDBOX_HOT result<std::uint64_t> handle::lock(seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::timeout_ok(lock_timeout, false))
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t locked = 0;
    const bool done = detail::retry(s, lock_timeout.count(), [&]() noexcept {
        std::uint64_t even = seq.load(std::memory_order_relaxed);
        if ((even & 1u) != 0 ||
            !seq.compare_exchange_strong(even, even + 1, std::memory_order_acquire, std::memory_order_relaxed))
            return false;
        // Keeps the record stores that follow from moving before the odd sequence number.
        std::atomic_thread_fence(std::memory_order_release);
        detail::atomic(s.hdr->writer_pid).store(current_pid(), std::memory_order_relaxed);
        locked = even;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return locked;
}

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

SHAREDBOX_HOT result<void> handle::write(std::span<const value> values, seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::values_ok({s.fields.get(), s.field_count}, values))
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
    const std::uint32_t pid = current_pid();
    const std::uint64_t start = current_start();
    const std::uint64_t pidns = current_pidns();
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
            detail::keep_os_error keep;
            detail::owned_clear(s, i);
            detail::free_slot(s, i);
            return unexpected(status::os);
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
            return unexpected(status::os);
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
        if (WaitForSingleObject(e, static_cast<DWORD>(ms) + 1) == WAIT_FAILED)
            return unexpected(status::os);
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
        return unexpected(status::os);
    SetEvent(e);
#else
    detail::atomic(s.hdr->wake_word).fetch_add(1, std::memory_order_seq_cst);
    syscall(SYS_futex, &s.hdr->wake_word, detail::futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
    return {};
}

} // namespace sharedbox

#endif
