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

/// The most keys a lookup scans; above it, the lookup probes the index.
inline constexpr std::uint32_t scan_limit = 8;
/// No slot; a capacity is at most 2**20, so no slot index reaches it.
inline constexpr std::uint32_t no_slot = 0xffffffff;

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
    /// no bit is masked, so keys compare whole.
    std::vector<std::byte> mask;
    /// Empty for a key of int, str or enum, whose stored bytes are compared as they are.
    std::vector<KeyCheck> checks;
    /// The size of a key, at the start of its slot.
    std::uint32_t size = 0;
};

/// A hash table of slot positions over the keys of a value, built when first needed. An entry holds the slot
/// in its low 32 bits and the high 32 bits of its key's hash above, or all ones when empty; a probe starts at
/// those hash bits under mask and goes on linearly.
struct KeyIndex {
    std::unique_ptr<std::uint64_t[]> table;
    std::uint32_t mask = 0;
    bool built = false;
};

/// The plan of keys of type key, from the members tree gives it, records and optionals included.
KeyPlan plan_of(const detail::type_table &tree, const detail::type_ref &key);

/// The slots of a value whose elements are keys: a set's elements, or a dict's keys with their values. Keys
/// compare by their stored bytes, masked by the plan; a lookup scans at scan_limit keys or fewer, and above
/// that probes an index built on the first lookup. Unless a method says otherwise, the caller holds the mutex.
class KeyedStore : public SlotStore {
protected:
    KeyedStore(nb::object types, const detail::type_ref &node, std::string label,
               std::span<const std::byte> stored, std::unique_ptr<std::byte[]> *owned);
    ~KeyedStore() = default;

    /// Zero-fills stride_ bytes at out and encodes value there as a key, as a write would encode it, then
    /// checks it and stores a -0.0 as 0.0. Raises ValueError for a nan, a NaT or an aware datetime or time,
    /// or, when quiet, returns false instead of raising TypeError, ValueError or OverflowError. Runs without
    /// the mutex.
    bool encode_key(nb::handle value, std::byte *out, bool quiet) const;
    /// The slot holding key, or no_slot.
    std::uint32_t find(const std::byte *key);
    /// The slot of a set holding key, or no_slot. The first lookup on keys not yet checked compares key with
    /// every slot through scan and checks each as ensure_checked does, but drops no duplicate and builds no
    /// index, since a value read for one test or change gains nothing from them; a later one runs
    /// ensure_checked and find.
    std::uint32_t lookup(const std::byte *key);
    /// Removes key from a set and returns whether it held it. On the first lookup, whose keys may hold a
    /// duplicate, it removes every slot holding key.
    bool remove_key(const std::byte *key);
    /// Appends a slot holding encoded and indexes it; the caller has checked the capacity.
    void append_slot(const std::byte *encoded);
    /// Moves the last slot into slot at, which it removes.
    void remove_moving_last(std::uint32_t at);
    /// Drops the index, which the next lookup above scan_limit keys builds again.
    void forget();
    /// Checks the stored keys once, before the first lookup: raises the corrupt-value ValueError for one a
    /// key cannot hold, stores a -0.0 as 0.0, and drops a later duplicate, or for a dict, whose values start
    /// at value_offset in a slot, keeps its value in the first. value_offset 0 means a set.
    void ensure_checked(std::uint32_t value_offset);

    KeyIndex index_;
    /// Whether the stored keys have passed ensure_checked; a value that starts empty has.
    bool checked_ = false;
    /// Whether a lookup has scanned the keys while they were not yet checked.
    bool scanned_ = false;
    const KeyPlan *plan_ = nullptr;
    std::uint32_t key_size_ = 0;

private:
    /// The end of the message saying why key, at the start of a slot, is refused, or nullptr when it is not;
    /// stores a -0.0 in it as 0.0.
    const char *canonical(std::byte *key) const;
    /// The hash of key, masked through scratch, key_size_ bytes, when the plan has checks.
    std::uint64_t hash_of(const std::byte *key, std::byte *scratch) const;
    bool same(const std::byte *a, const std::byte *b) const;
    /// The last slot before end holding key, or no_slot, comparing key with each slot.
    std::uint32_t scan(const std::byte *key, std::uint32_t end) const;
    /// Builds the index over every slot, at a power of two of entries at least twice the count.
    void build();
    /// Replaces the index with an empty one of a power of two of entries at least twice keys.
    void allocate(std::uint32_t keys);
    /// Adds slot at, whose key hashes to hash, to a built index, rebuilding it twice as large past half full.
    void insert(std::uint32_t at, std::uint64_t hash);
    /// Writes the entry of slot at in the first empty position of its probe.
    void place(std::uint32_t at, std::uint64_t hash);
    /// The table position of slot at's entry in a built index.
    std::uint32_t entry_of(std::uint32_t at, std::byte *scratch) const;
    /// Removes the entry at table position p, moving later entries of its run back so no probe stops early.
    void erase(std::uint32_t p);
};

} // namespace sharedbox
