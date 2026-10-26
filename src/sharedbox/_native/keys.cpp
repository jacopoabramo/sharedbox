#include "keys.hpp"

#include <algorithm>
#include <bit>
#include <cstring>
#include <functional>
#include <limits>
#include <string_view>

#include "types.hpp"

namespace sharedbox {
namespace {

/// A table entry holding no slot.
constexpr std::uint64_t empty_entry = ~std::uint64_t{0};
constexpr std::uint64_t tag_bits = 0xffffffff00000000;

std::uint32_t tag_of(std::uint64_t hash) { return static_cast<std::uint32_t>(hash >> 32); }

/// Whether the IEEE 754 number whose bits at p are of type Bits is not a nan; a -0.0 there becomes 0.0.
template <typename Bits> bool number_key(std::byte *p, Bits exponent, Bits fraction) {
    Bits bits;
    std::memcpy(&bits, p, sizeof bits);
    if ((bits & exponent) == exponent && (bits & fraction) != 0)
        return false;
    if (bits == Bits(Bits{1} << (sizeof(Bits) * 8 - 1))) {
        bits = 0;
        std::memcpy(p, &bits, sizeof bits);
    }
    return true;
}

bool double_key(std::byte *p) { return number_key<std::uint64_t>(p, 0x7ff0000000000000, 0x000fffffffffffff); }

bool float_key(std::byte *p) { return number_key<std::uint32_t>(p, 0x7f800000, 0x007fffff); }

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
    plan.size = key.size;
    plan.mask.assign(key.size, std::byte{0xff});
    add_checks(tree, key, 0, no_guard, plan);
    if (plan.checks.empty())
        plan.mask.clear();
    return plan;
}

KeyedStore::KeyedStore(nb::object types, const detail::type_ref &node, std::string label,
                       std::span<const std::byte> stored, std::unique_ptr<std::byte[]> *owned)
    : SlotStore(std::move(types), node, std::move(label), stored, owned), checked_(count_ == 0),
      plan_(&codec().key_plan(node_)), key_size_(plan_->size) {}

const char *KeyedStore::canonical(std::byte *key) const {
    for (const KeyCheck &c : plan_->checks) {
        if (c.guard != no_guard && key[c.guard] != std::byte{1})
            continue;
        std::byte *at = key + c.offset;
        switch (c.kind) {
        case kind_float:
            if (!double_key(at))
                return "takes no nan keys";
            break;
        case kind_complex:
            if (!double_key(at) || !double_key(at + 8))
                return "takes no nan keys";
            break;
        case kind_float32:
            if (!float_key(at))
                return "takes no nan keys";
            break;
        case kind_complex64:
            if (!float_key(at) || !float_key(at + 4))
                return "takes no nan keys";
            break;
        case kind_float16:
            if (!number_key<std::uint16_t>(at, 0x7c00, 0x03ff))
                return "takes no nan keys";
            break;
        case kind_datetime64: {
            std::int64_t ticks;
            std::memcpy(&ticks, at, sizeof ticks);
            if (ticks == std::numeric_limits<std::int64_t>::min())
                return "takes no NaT keys";
            break;
        }
        case kind_datetime:
        case kind_time:
            // Byte 10 holds the flags, bit 0 naive.
            if ((at[10] & std::byte{1}) == std::byte{0})
                return c.kind == kind_datetime ? "takes only naive datetime keys" : "takes only naive time keys";
            break;
        default:
            break;
        }
    }
    return nullptr;
}

bool KeyedStore::encode_key(nb::handle value, std::byte *out, bool quiet) const {
    std::memset(out, 0, stride_);
    if (!quiet) {
        codec().encode_element(node_, value.ptr(), out, label_);
        if (const char *refused = canonical(out))
            raise(PyExc_ValueError, label_ + " " + refused);
        return true;
    }
    try {
        codec().encode_element(node_, value.ptr(), out, label_);
    } catch (nb::python_error &error) {
        if (!error.matches(PyExc_TypeError) && !error.matches(PyExc_ValueError) &&
            !error.matches(PyExc_OverflowError))
            throw;
        return false;
    }
    return canonical(out) == nullptr;
}

std::uint64_t KeyedStore::hash_of(const std::byte *key, std::byte *scratch) const {
    const std::byte *hashed = key;
    if (!plan_->mask.empty()) {
        for (std::uint32_t i = 0; i < key_size_; ++i)
            scratch[i] = key[i] & plan_->mask[i];
        hashed = scratch;
    }
    return std::hash<std::string_view>{}(std::string_view(reinterpret_cast<const char *>(hashed), key_size_));
}

bool KeyedStore::same(const std::byte *a, const std::byte *b) const {
    if (plan_->mask.empty())
        return std::memcmp(a, b, key_size_) == 0;
    for (std::uint32_t i = 0; i < key_size_; ++i)
        if (((a[i] ^ b[i]) & plan_->mask[i]) != std::byte{0})
            return false;
    return true;
}

std::uint32_t KeyedStore::find(const std::byte *key) {
    if (count_ <= scan_limit) {
        for (std::uint32_t i = 0; i < count_; ++i)
            if (same(slot(i), key))
                return i;
        return no_slot;
    }
    if (!index_.built)
        build();
    SlotBuffer scratch(key_size_);
    const std::uint32_t tag = tag_of(hash_of(key, scratch.data()));
    for (std::uint32_t p = tag & index_.mask;; p = (p + 1) & index_.mask) {
        const std::uint64_t entry = index_.table[p];
        if (entry == empty_entry)
            return no_slot;
        const auto at = static_cast<std::uint32_t>(entry);
        if (tag_of(entry) == tag && same(slot(at), key))
            return at;
    }
}

void KeyedStore::build() {
    const std::uint32_t size = std::bit_ceil(std::max<std::uint32_t>(2 * count_, 2));
    index_.table.reset(new std::uint64_t[size]);
    std::fill_n(index_.table.get(), size, empty_entry);
    index_.mask = size - 1;
    index_.built = true;
    SlotBuffer scratch(key_size_);
    for (std::uint32_t at = 0; at < count_; ++at)
        place(at, hash_of(slot(at), scratch.data()));
}

void KeyedStore::place(std::uint32_t at, std::uint64_t hash) {
    // An entry's position comes from the hash bits it keeps, so erase finds an entry's first position without
    // hashing its key again.
    std::uint32_t p = tag_of(hash) & index_.mask;
    while (index_.table[p] != empty_entry)
        p = (p + 1) & index_.mask;
    index_.table[p] = (hash & tag_bits) | at;
}

void KeyedStore::insert(std::uint32_t at, std::uint64_t hash) {
    if (2 * std::size_t{count_} > std::size_t{index_.mask} + 1)
        return build();
    place(at, hash);
}

std::uint32_t KeyedStore::entry_of(std::uint32_t at, std::byte *scratch) const {
    std::uint32_t p = tag_of(hash_of(slot(at), scratch)) & index_.mask;
    while (static_cast<std::uint32_t>(index_.table[p]) != at)
        p = (p + 1) & index_.mask;
    return p;
}

void KeyedStore::erase(std::uint32_t p) {
    std::uint64_t *table = index_.table.get();
    const std::uint32_t mask = index_.mask;
    for (std::uint32_t j = (p + 1) & mask; table[j] != empty_entry; j = (j + 1) & mask) {
        // The entry at j stays when its first position lies after p, up to j, since its probe never passes
        // p; otherwise it moves back into p, so the gap never cuts its probe short.
        const std::uint32_t first = tag_of(table[j]) & mask;
        if (((j - first) & mask) >= ((j - p) & mask)) {
            table[p] = table[j];
            p = j;
        }
    }
    table[p] = empty_entry;
}

void KeyedStore::append_slot(const std::byte *encoded) {
    splice(count_, count_, encoded, 1);
    if (!index_.built)
        return;
    SlotBuffer scratch(key_size_);
    insert(count_ - 1, hash_of(slot(count_ - 1), scratch.data()));
}

void KeyedStore::remove_moving_last(std::uint32_t at) {
    const std::uint32_t last = count_ - 1;
    if (index_.built) {
        SlotBuffer scratch(key_size_);
        erase(entry_of(at, scratch.data()));
        if (at != last) {
            std::uint64_t &moved = index_.table[entry_of(last, scratch.data())];
            moved = (moved & tag_bits) | at;
        }
    }
    if (at != last)
        std::memcpy(slot(at), slot(last), stride_);
    set_count(last);
}

void KeyedStore::forget() { index_ = KeyIndex{}; }

void KeyedStore::ensure_checked(std::uint32_t value_offset) {
    // Raises for a value the collector cleared, whose plan_ points into the Types it no longer holds.
    codec();
    if (checked_)
        return;
    // Every key is checked before any slot moves, so a refused one leaves the count as it was.
    for (std::uint32_t at = 0; at < count_; ++at)
        if (canonical(slot(at)) != nullptr)
            corrupt(label_);
    const std::uint32_t stored = count_;
    forget();
    set_count(0);
    SlotBuffer scratch(key_size_);
    for (std::uint32_t at = 0; at < stored; ++at) {
        std::byte *key = slot(at);
        if (const std::uint32_t first = find(key); first != no_slot) {
            if (value_offset != 0)
                std::memcpy(slot(first) + value_offset, key + value_offset, stride_ - value_offset);
            continue;
        }
        const std::uint32_t kept = count_;
        if (kept != at)
            std::memcpy(slot(kept), key, stride_);
        set_count(kept + 1);
        if (index_.built)
            insert(kept, hash_of(slot(kept), scratch.data()));
    }
    checked_ = true;
}

} // namespace sharedbox
