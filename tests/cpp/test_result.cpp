// sharedbox::result: the header's own type on C++20, std::expected<T, status> where the library has it.
// Both must behave the same for the calls this library and its users make.
#include "check.hpp"

#include <memory>
#include <type_traits>

using sharedbox::result;
using sharedbox::status;

namespace {

#if defined(__cpp_lib_expected) && __cpp_lib_expected >= 202202L
static_assert(std::is_same_v<result<int>, std::expected<int, status>>);
constexpr const char *flavour = "result (std::expected)";
#else
constexpr const char *flavour = "result (C++20)";
#endif

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

void test_value_and_error() {
    const result<int> ok = half(8);
    CHECK(ok.has_value() && static_cast<bool>(ok) && *ok == 4 && ok.value() == 4);
    const result<int> bad = half(3);
    CHECK(!bad && bad.error() == status::range);
    CHECK(bad.value_or(-1) == -1 && ok.value_or(-1) == 4);
}

void test_chaining() {
    CHECK(*half(8).and_then(half) == 2);
    CHECK(half(6).and_then(half).error() == status::range);
    CHECK(half(3).and_then(half).error() == status::range);
    CHECK(*half(8).transform([](int n) { return n * 10; }) == 40);
    CHECK(half(3).transform([](int n) { return n * 10; }).error() == status::range);
    CHECK(*half(3).or_else([](status) -> result<int> { return 0; }) == 0);
    CHECK(*half(8).or_else([](status) -> result<int> { return 0; }) == 4);
    int calls = 0;
    CHECK(half(8).transform([&](int) { ++calls; }).has_value() && calls == 1);
    CHECK(check_positive(1).and_then([] { return half(8); }).value_or(0) == 4);
    CHECK(check_positive(0).and_then([] { return half(8); }).error() == status::range);
    CHECK(check_positive(0).error() == status::range && check_positive(1).has_value());
}

void test_move_only_values() {
    result<std::unique_ptr<int>> made = std::make_unique<int>(7);
    CHECK(made && **made == 7);
    std::unique_ptr<int> taken = *std::move(made);
    CHECK(taken != nullptr && *taken == 7);
    const result<std::unique_ptr<int>> failed = sharedbox::unexpected(status::os);
    CHECK(failed.error() == status::os);
}

} // namespace

int main() {
    test_value_and_error();
    test_chaining();
    test_move_only_values();
    return finish(flavour);
}
