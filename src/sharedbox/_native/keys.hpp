#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>
#include <sharedbox/sharedbox.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "collection.hpp"

namespace sharedbox {

/// The guard of a key check that applies whatever the other bytes hold.
inline constexpr std::uint32_t no_guard = static_cast<std::uint32_t>(-1);

/// A member of a key whose stored bytes need checking: a float, complex, datetime, time or datetime64 at
/// offset. A check inside an optional runs only when the byte at guard, its presence byte, is 1.
struct KeyCheck {
    std::uint32_t offset;
    std::uint8_t kind;
    std::uint32_t guard = no_guard;
};

/// How the bytes of a key are checked and compared.
struct KeyPlan {
    /// The bytes that count when keys are compared: 0xff except each datetime and time fold bit. Empty when
    /// there are no checks, so keys compare whole.
    std::vector<std::byte> mask;
    /// Empty for a key of int, str or enum, whose stored bytes are compared as they are.
    std::vector<KeyCheck> checks;
};

/// A hash table of slot positions over the keys of a value, built when first needed.
struct KeyIndex {
    std::unique_ptr<std::uint64_t[]> table;
    std::uint32_t mask = 0;
    bool built = false;
};

/// The plan of keys of type key, from the members tree gives it, records and optionals included.
KeyPlan plan_of(const detail::type_table &tree, const detail::type_ref &key);

/// The slots of a value whose elements are keys: a set's elements, or a dict's keys with their values.
class KeyedStore : public SlotStore {
protected:
    KeyedStore(nb::object types, const detail::type_ref &node, std::string label,
               std::span<const std::byte> stored, std::unique_ptr<std::byte[]> *owned)
        : SlotStore(std::move(types), node, std::move(label), stored, owned) {}
    ~KeyedStore() = default;
};

} // namespace sharedbox
