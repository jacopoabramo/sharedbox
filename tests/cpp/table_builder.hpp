// Builds description tables byte by byte, for tests that need valid and forged ones.
#pragma once

#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>
#include <span>
#include <string_view>
#include <utility>
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

// A max_array description of uint16 at most 2048 x 2048, or of the given dimensions and head size.
inline void max_array(table_builder &t, std::vector<std::uint64_t> dims = {2048, 2048}, std::uint32_t size = 0,
                      std::uint8_t reserved = 0) {
    std::uint64_t data = 2;
    for (const std::uint64_t d : dims)
        data *= d;
    t.head(sharedbox::kind_max_array, 0, size != 0 ? size : static_cast<std::uint32_t>(64 + data));
    t.put(sharedbox::dl_dtype{1, 16, 1});
    t.put(static_cast<std::uint8_t>(dims.size()));
    t.put(reserved);
    t.put(std::uint16_t{0});
    for (const std::uint64_t d : dims)
        t.put(d);
}

// kind, holding a max_array of uint16 at most 2048 x 2048 as its one member.
inline table_builder around_max_array(std::uint8_t kind) {
    table_builder t;
    const std::uint32_t data = 64 + 2048 * 2048 * 2;
    // A record or tuple member starts at 0; an optional's follows its presence byte and a list's element its
    // count, each padded to the alignment of 64.
    const bool first = kind == sharedbox::kind_record || kind == sharedbox::kind_tuple;
    t.head(kind, 1, first ? data : 64 + data);
    if (kind == sharedbox::kind_list)
        t.put(std::uint32_t{1});
    const std::size_t entry = t.bytes.size();
    t.entry(first ? 0 : 64, sharedbox::kind_max_array, 0);
    if (kind == sharedbox::kind_record)
        t.name("a");
    t.pad();
    const auto at = static_cast<std::uint32_t>(t.bytes.size());
    max_array(t);
    t.patch(entry + 4, entry_of(sharedbox::kind_max_array, at));
    return t;
}

// A timed description (kind 77) around one member entry at member_offset. The head size is the one the
// layout gives for a member of the stated size and alignment, unless size is not 0.
inline table_builder timed(std::uint8_t member_kind, std::uint32_t member_low, std::uint32_t size = 0,
                           std::uint16_t count = 1, std::uint32_t member_offset = 0) {
    table_builder t;
    const std::uint32_t alignment = (std::max)(sharedbox::detail::kind_alignment(member_kind), 8u);
    const std::uint64_t exact =
        sharedbox::detail::round_up(sharedbox::detail::round_up(member_low, 8) + 8, alignment);
    t.head(sharedbox::kind_timed, count, size != 0 ? size : static_cast<std::uint32_t>(exact));
    for (std::uint16_t i = 0; i < count; ++i)
        t.entry(member_offset, member_kind, member_low);
    return t;
}

// Appends a timed description around a described member: a record of one uint8 for kind_record, or a
// max_array as max_array() builds it for kind_max_array.
inline void timed_around(table_builder &t, std::uint8_t kind) {
    const bool record = kind == sharedbox::kind_record;
    const std::uint32_t member = record ? 1 : 64 + 2048 * 2048 * 2;
    const std::uint32_t alignment = record ? 8 : 64;
    t.head(sharedbox::kind_timed, 1,
           static_cast<std::uint32_t>(
               sharedbox::detail::round_up(sharedbox::detail::round_up(member, 8) + 8, alignment)));
    const std::size_t entry = t.bytes.size();
    t.entry(0, kind, 0);
    const auto at = static_cast<std::uint32_t>(t.bytes.size());
    if (record) {
        t.head(sharedbox::kind_record, 1, member);
        t.entry(0, sharedbox::kind_uint8, 1);
        t.name("a");
        t.pad();
    } else {
        max_array(t);
    }
    t.patch(entry + 4, entry_of(kind, at));
}

// An attr description (kind 76) of an optional value type T and fixed-kind arguments, each a kind and a
// capacity, with the offsets and the size the layout gives for the flags. A test forges one by patching the
// bytes: the head size at 4, the argument count at 8, entry i at 16 + 8 * i, which is T first when there is
// one and then the arguments.
inline table_builder attr(std::optional<std::pair<std::uint8_t, std::uint32_t>> value,
                          std::vector<std::pair<std::uint8_t, std::uint32_t>> arguments, std::uint8_t flags) {
    using sharedbox::detail::kind_alignment;
    using sharedbox::detail::prefixed;
    using sharedbox::detail::round_up;
    using member = std::pair<std::uint8_t, std::uint32_t>;
    const auto size_of = [](const member &m) { return prefixed(m.first) ? 4u + m.second : m.second; };
    const auto alignment_of = [](const member &m) -> std::uint64_t {
        return prefixed(m.first) ? 4u : kind_alignment(m.first);
    };
    const bool with_timestamp = (flags & sharedbox::attr_timed) != 0;
    const bool settable = (flags & sharedbox::attr_settable) != 0;
    const bool request = (flags & sharedbox::attr_request) != 0;
    const std::uint64_t value_alignment = value ? (std::max<std::uint64_t>)(8, alignment_of(*value)) : 8;
    std::uint64_t field_alignment = value_alignment, end = 48;
    std::vector<std::uint32_t> offsets;
    for (const member &argument : arguments) {
        offsets.push_back(static_cast<std::uint32_t>(round_up(end, alignment_of(argument))));
        end = offsets.back() + size_of(argument);
        field_alignment = (std::max)(field_alignment, alignment_of(argument));
    }
    std::uint64_t request_end = 0;
    if (request) {
        const std::uint64_t result_at = value ? round_up(272, alignment_of(*value)) : 272;
        request_end = round_up(end, value_alignment) +
                      4 * round_up(result_at + (value ? size_of(*value) : 0), value_alignment);
    }
    std::uint64_t value_offset = 0, last_end = request_end;
    if (value) {
        value_offset = round_up(round_up(request_end, value_alignment) + 8, alignment_of(*value));
        last_end = value_offset + (with_timestamp ? round_up(size_of(*value), 8) + 8 : size_of(*value));
        if (settable)
            last_end = round_up(last_end, alignment_of(*value)) + size_of(*value);
    }
    table_builder t;
    t.head(sharedbox::kind_attr, static_cast<std::uint16_t>(arguments.size() + (value ? 1 : 0)),
           static_cast<std::uint32_t>(round_up(last_end, field_alignment)), flags);
    t.put(static_cast<std::uint16_t>(arguments.size()));
    t.pad();
    if (value)
        t.entry(static_cast<std::uint32_t>(value_offset), value->first, value->second);
    for (std::size_t i = 0; i < arguments.size(); ++i)
        t.entry(offsets[i], arguments[i].first, arguments[i].second);
    return t;
}
