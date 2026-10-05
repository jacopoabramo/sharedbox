// Builds description tables byte by byte, for tests that need valid and forged ones.
#pragma once

#include <sharedbox/sharedbox.hpp>

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <string_view>
#include <vector>

struct table_builder {
    std::vector<std::byte> bytes;

    template <class T> void put(const T &value) {
        const auto *p = reinterpret_cast<const std::byte *>(&value);
        bytes.insert(bytes.end(), p, p + sizeof value);
    }
    // Starts a description and returns its offset.
    std::uint32_t head(std::uint8_t kind, std::uint16_t count, std::uint32_t size, std::uint8_t flags = 0) {
        const auto at = static_cast<std::uint32_t>(bytes.size());
        put(sharedbox::type_head{kind, flags, count, size});
        return at;
    }
    void name(std::string_view text) {
        put(static_cast<std::uint16_t>(text.size()));
        for (const char c : text)
            bytes.push_back(static_cast<std::byte>(c));
    }
    // An entry: an offset within the parent, then the kind and a size, capacity or description offset.
    void entry(std::uint32_t offset, std::uint8_t kind, std::uint32_t low) {
        put(offset);
        put(low | std::uint32_t{kind} << sharedbox::kind_shift);
    }
    void pad() {
        while (bytes.size() % 8 != 0)
            bytes.push_back(std::byte{0});
    }
    // Overwrites the u32 at offset, for an entry whose description is written after it.
    void patch(std::size_t offset, std::uint32_t value) {
        std::memcpy(bytes.data() + offset, &value, sizeof value);
    }
    std::span<const std::byte> span() const { return bytes; }
};

inline std::uint32_t entry_of(std::uint8_t kind, std::uint32_t low) {
    return low | std::uint32_t{kind} << sharedbox::kind_shift;
}
