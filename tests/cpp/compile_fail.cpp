// Calls that must not compile. Each CTest case builds this file with one SBX_FAIL_ macro defined and
// expects the matching static_assert message in the compiler output.
#include <sharedbox/sharedbox.hpp>

namespace {

struct only_explicit_int {
    explicit operator int() const { return 0; }
};

} // namespace

int main() {
    const sharedbox::result<int> r = 1;
#if defined(SBX_FAIL_or_else_type)
    (void)r.or_else([](sharedbox::status) { return 0; });
#elif defined(SBX_FAIL_transform_reference)
    static int x = 0;
    (void)r.transform([](int) -> int & { return x; });
#elif defined(SBX_FAIL_value_or_explicit)
    (void)r.value_or(only_explicit_int{});
#endif
    return r.has_value() ? 0 : 1;
}
