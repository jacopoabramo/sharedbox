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

    void pause() noexcept {
        if (++spins_ < 64)
            cpu_relax();
        else
            std::this_thread::yield();
    }

private:
    double timeout_;
    clock::time_point deadline_{};
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

    ~state();
};

inline state::~state() = default;

// A state for a mapping of the given shape, or nullptr when memory runs out.
inline std::unique_ptr<state> make_state(std::string_view name, std::uint16_t field_count,
                                         std::uint16_t waiter_slots) noexcept {
    std::unique_ptr<state> s(new (std::nothrow) state);
    if (s == nullptr)
        return nullptr;
    s->fields.reset(new (std::nothrow) field_spec[field_count]);
    if (s->fields == nullptr)
        return nullptr;
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

} // namespace sharedbox

#endif
