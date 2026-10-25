#include "keys.hpp"

namespace sharedbox {
namespace {

void add_checks(const detail::type_table &tree, const detail::type_ref &t, std::uint32_t at, std::uint32_t guard,
                KeyPlan &plan) {
    switch (t.kind) {
    case kind_float:
    case kind_float16:
    case kind_float32:
    case kind_complex:
    case kind_complex64:
    case kind_datetime64:
        plan.checks.push_back({at, t.kind, guard});
        return;
    case kind_time:
    case kind_datetime:
        plan.checks.push_back({at, t.kind, guard});
        // Byte 10 holds the flags; bit 1, the fold, does not change which value a naive key is.
        plan.mask[at + 10] &= std::byte{0xfd};
        return;
    case kind_optional: {
        // An absent optional's bytes are zero, so only the nearest presence byte needs testing.
        const detail::type_member &inner = tree.member(tree.node(t.node).members);
        add_checks(tree, inner.type, at + inner.offset, at, plan);
        return;
    }
    case kind_record:
    case kind_tuple: {
        const detail::type_node &node = tree.node(t.node);
        for (std::uint32_t i = 0; i < node.count; ++i) {
            const detail::type_member &m = tree.member(node.members + i);
            add_checks(tree, m.type, at + m.offset, guard, plan);
        }
        return;
    }
    default:
        return;
    }
}

} // namespace

KeyPlan plan_of(const detail::type_table &tree, const detail::type_ref &key) {
    KeyPlan plan;
    plan.mask.assign(key.size, std::byte{0xff});
    add_checks(tree, key, 0, no_guard, plan);
    if (plan.checks.empty())
        plan.mask.clear();
    return plan;
}

} // namespace sharedbox
