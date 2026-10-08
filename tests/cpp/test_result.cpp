// sharedbox::result: the header's own type whatever the standard.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <cerrno>
#include <memory>
#include <type_traits>

#ifdef SBX_EXPECT_STD_EXPECTED
#include <expected>
#endif

using sharedbox::result;
using sharedbox::status;

namespace {

// result is the header's own class whatever the standard, so C++20 and C++23 translation units of
// one program see one definition.
#ifdef SBX_EXPECT_STD_EXPECTED
static_assert(!std::is_same_v<result<int>, std::expected<int, sharedbox::error>>);
static_assert(std::is_constructible_v<std::expected<int, sharedbox::error>, result<int>>);
static_assert(std::is_constructible_v<result<int>, std::expected<int, sharedbox::error>>);
#endif

// windows.h defines min and max as macros unless NOMINMAX is set first, which breaks this line.
static_assert(std::max(1, 2) == 2);

// The header removes the macros it defined, so they do not reach code that includes it.
#if defined(SHAREDBOX_HOT) || (defined(_WIN32) && (defined(NOMINMAX) || defined(WIN32_LEAN_AND_MEAN)))
#error "sharedbox.hpp left one of its own macros defined"
#endif

// The C++ names live in a versioned inline namespace, reachable without naming it.
static_assert(std::is_same_v<sharedbox::handle, sharedbox::v3::handle>);
static_assert(sizeof(sharedbox::error) == 16 && std::is_trivially_copyable_v<sharedbox::error>);

// A value that converts to T only explicitly gives an explicit constructor, as std::expected does.
struct only_explicit {
    explicit only_explicit(int) {}
};
static_assert(std::is_constructible_v<result<only_explicit>, int>);
static_assert(!std::is_convertible_v<int, result<only_explicit>>);
static_assert(std::is_convertible_v<int, result<long>>);

// result has no converting constructor from another result, so result<bool> must not be built from one
// through its explicit operator bool.
static_assert(!std::is_constructible_v<result<bool>, result<int>>);

result<int> half(int n) {
    if (n % 2 != 0)
        return sharedbox::unexpected(status::range);
    return n / 2;
}

result<void> check_positive(int n) {
    if (n <= 0)
        return sharedbox::unexpected(status::range);
    return {};
}

TEST_CASE("value and error") {
    const result<int> ok = half(8);
    CHECK((ok.has_value() && static_cast<bool>(ok) && *ok == 4 && ok.value() == 4));
    const result<int> bad = half(3);
    CHECK((!bad && bad.error().code == status::range));
    CHECK((bad.value_or(-1) == -1 && ok.value_or(-1) == 4));
}

TEST_CASE("chaining") {
    CHECK(*half(8).and_then(half) == 2);
    CHECK(half(6).and_then(half).error().code == status::range);
    CHECK(half(3).and_then(half).error().code == status::range);
    CHECK(*half(8).transform([](int n) { return n * 10; }) == 40);
    CHECK(half(3).transform([](int n) { return n * 10; }).error().code == status::range);
    CHECK(*half(3).or_else([](const sharedbox::error &) -> result<int> { return 0; }) == 0);
    CHECK(*half(8).or_else([](const sharedbox::error &) -> result<int> { return 0; }) == 4);
    int calls = 0;
    CHECK((half(8).transform([&](int) { ++calls; }).has_value() && calls == 1));
    CHECK(check_positive(1).and_then([] { return half(8); }).value_or(0) == 4);
    CHECK(check_positive(0).and_then([] { return half(8); }).error().code == status::range);
    CHECK((check_positive(0).error().code == status::range && check_positive(1).has_value()));
}

TEST_CASE("lvalue calls") {
    result<int> ok = half(8);
    const result<int> bad = half(3);
    const auto zero = [](const sharedbox::error &) -> result<int> { return 0; };
    CHECK((*ok.and_then(half) == 2 && bad.and_then(half).error().code == status::range));
    CHECK(*ok.transform([](int n) { return n + 1; }) == 5);
    CHECK(bad.transform([](int n) { return n + 1; }).error().code == status::range);
    CHECK((*ok.or_else(zero) == 4 && *bad.or_else(zero) == 0));
    result<void> done;
    const result<void> failed = sharedbox::unexpected(status::os);
    CHECK(done.and_then([] { return half(8); }).value_or(0) == 4);
    CHECK(failed.and_then([] { return half(8); }).error().code == status::os);
    CHECK(
        (*done.transform([] { return 1; }) == 1 && failed.transform([] { return 1; }).error().code == status::os));
    CHECK(failed.or_else([](const sharedbox::error &) -> result<void> { return {}; }).has_value());
}

// An error is an error whatever its status, status::ok included, as with std::unexpected.
TEST_CASE("an error with status ok is still an error") {
    const result<void> v = sharedbox::unexpected(status::ok);
    CHECK((!v.has_value() && v.error().code == status::ok));
    const result<int> i = sharedbox::unexpected(status::ok);
    CHECK((!i.has_value() && i.error().code == status::ok));
}

TEST_CASE("move only values") {
    result<std::unique_ptr<int>> made = std::make_unique<int>(7);
    CHECK((made && **made == 7));
    std::unique_ptr<int> taken = *std::move(made);
    CHECK((taken != nullptr && *taken == 7));
    const result<std::unique_ptr<int>> failed = sharedbox::unexpected(status::os);
    CHECK(failed.error().code == status::os);
}

} // namespace

TEST_CASE("an error carries its code, the OS error and what was found") {
    const result<int> failed = sharedbox::unexpected(sharedbox::error{status::os, 13, 0});
    REQUIRE_FALSE(failed);
    CHECK(failed.error().code == status::os);
    CHECK(failed.error().os == 13);
    const result<int> plain = sharedbox::unexpected(status::range);
    CHECK(plain.error().code == status::range);
    CHECK(plain.error().os == 0);
    CHECK(plain.error().found == 0);
}

TEST_CASE("os_failure captures the OS error of the failing call") {
#ifdef _WIN32
    SetLastError(ERROR_ACCESS_DENIED);
    const sharedbox::unexpected failed = sharedbox::detail::os_failure();
    CHECK(failed.error().os == ERROR_ACCESS_DENIED);
#else
    errno = EACCES;
    const sharedbox::unexpected failed = sharedbox::detail::os_failure();
    CHECK(failed.error().os == EACCES);
#endif
    CHECK(failed.error().code == status::os);
}
