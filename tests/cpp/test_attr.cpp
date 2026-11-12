// Attr fields (kind 76): the description, the steps that publish and read the value, and the protocol steps a
// client and a server take on a request.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <future>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

using namespace sharedbox;
using sharedbox::detail::type_table;

namespace {

constexpr seconds patience{1.0};
constexpr std::uint64_t server_start_at = 24;

status parse(const table_builder &t, type_table &table) {
    const std::uint32_t entry = entry_of(kind_attr, 0);
    return table.parse(t.span(), {&entry, 1}, true);
}

status parse(const table_builder &t) {
    type_table table;
    return parse(t, table);
}

std::uint64_t seq_of(const handle &box) { return static_cast<const header *>(box.base())->seq; }

std::array<std::byte, 8> eight(std::int64_t number) {
    std::array<std::byte, 8> bytes{};
    std::memcpy(bytes.data(), &number, sizeof number);
    return bytes;
}

std::int64_t number_of(std::span<const std::byte> bytes) {
    std::int64_t number = 0;
    std::memcpy(&number, bytes.data(), sizeof number);
    return number;
}

std::string_view text_of(const std::array<std::byte, 256> &out, std::size_t length) {
    return {reinterpret_cast<const char *>(out.data()), length};
}

handle make(const std::string &name, const table_builder &t) {
    std::uint32_t size = 0;
    std::memcpy(&size, t.bytes.data() + 4, sizeof size);
    const field_spec fields[1] = {{0, 0, kind_attr}};
    auto box = handle::create(name, fields, size, 1, 4, {}, t.span());
    REQUIRE(box.has_value());
    return std::move(*box);
}

table_builder int_request() { return attr({{kind_int, 8}}, {{kind_int, 8}}, attr_request); }

table_builder everything() { return attr({{kind_int, 8}}, {{kind_int, 8}}, 7); }

constexpr std::size_t everything_parts = 32;

int hooks_before = 0;
int hooks_after = 0;

void count_hooks(handle &box) {
    hooks_before = hooks_after = 0;
    box.set_wait_hooks(
        []() -> void * {
            ++hooks_before;
            return nullptr;
        },
        [](void *) { ++hooks_after; });
}

std::array<std::byte, everything_parts> parts(std::int64_t published, std::int64_t value, std::int64_t timestamp,
                                              std::int64_t setpoint) {
    std::array<std::byte, everything_parts> bytes{};
    const std::int64_t numbers[4] = {published, value, timestamp, setpoint};
    std::memcpy(bytes.data(), numbers, sizeof numbers);
    return bytes;
}

std::int64_t at(const std::array<std::byte, everything_parts> &bytes, std::size_t index) {
    return number_of(std::span(bytes).subspan(index * 8, 8));
}

std::array<std::byte, everything_parts> value_part(const handle &box) {
    std::array<std::byte, everything_parts> out{};
    REQUIRE(box.attr_read(0, out).has_value());
    return out;
}

} // namespace

TEST_CASE("an attr description parses with the size of its parts") {
    constexpr std::uint32_t none = detail::attr_absent;
    struct shape {
        table_builder table;
        std::uint8_t flags;
        std::uint32_t size, value_at, value, setpoint_at;
    };
    const std::vector<shape> shapes = {
        {attr({{kind_int, 8}}, {}, 0), 0, 16, 0, 8, none},
        {attr({{kind_int, 8}}, {}, attr_timed), attr_timed, 24, 0, 8, none},
        {int_request(), attr_request, 1192, 1176, 1184, none},
        {attr({{kind_int, 8}}, {}, attr_request), attr_request, 1184, 1168, 1176, none},
        {attr({{kind_int, 8}}, {{kind_int, 8}}, attr_settable | attr_request), attr_settable | attr_request, 1200,
         1176, 1184, 1192},
        {everything(), 7, 1208, 1176, 1184, 1200},
        {attr(std::nullopt, {}, attr_request), attr_request, 1136, none, none, none},
    };
    for (const shape &s : shapes) {
        INFO("flags ", int{s.flags});
        type_table table;
        REQUIRE(parse(s.table, table) == status::ok);
        const detail::type_node &node = table.node(table.field(0).node);
        CHECK(
            (table.field(0).kind == kind_attr && table.field(0).size == s.size && table.field(0).alignment == 8));
        CHECK((node.flags == s.flags && node.value_at == s.value_at && node.setpoint_at == s.setpoint_at));
        if (s.value != none)
            CHECK(table.member(node.members).offset == s.value);
    }

    type_table table;
    REQUIRE(parse(int_request(), table) == status::ok);
    const detail::type_node &node = table.node(table.field(0).node);
    CHECK((node.capacity == 1 && node.slots == 56 && node.stride == 280));
    CHECK((table.member(node.members).offset == 1184 && table.member(node.members + 1).offset == 48));

    SUBCASE("flags 8") { CHECK(parse(attr({{kind_int, 8}}, {}, 8)) == status::corrupt); }
    SUBCASE("settable without a request part") {
        CHECK(parse(attr({{kind_int, 8}}, {}, attr_settable)) == status::corrupt);
    }
    SUBCASE("settable with two arguments") {
        CHECK(parse(attr({{kind_int, 8}}, {{kind_int, 8}, {kind_int, 8}}, attr_settable | attr_request)) ==
              status::corrupt);
    }
    SUBCASE("settable whose argument is not T") {
        CHECK(parse(attr({{kind_int, 8}}, {{kind_float, 8}}, attr_settable | attr_request)) == status::corrupt);
    }
    SUBCASE("timed without a value") {
        CHECK(parse(attr(std::nullopt, {}, attr_timed | attr_request)) == status::corrupt);
    }
    SUBCASE("settable without a value") {
        CHECK(parse(attr(std::nullopt, {{kind_int, 8}}, attr_settable | attr_request)) == status::corrupt);
    }
    SUBCASE("arguments without a request part") {
        CHECK(parse(attr({{kind_int, 8}}, {{kind_int, 8}}, 0)) == status::corrupt);
    }
    SUBCASE("neither a value nor a request part") { CHECK(parse(attr(std::nullopt, {}, 0)) == status::corrupt); }
    SUBCASE("a timed as an argument") {
        table_builder t = attr(std::nullopt, {{kind_int, 16}}, attr_request);
        const table_builder inner = timed(kind_int, 8);
        t.patch(20, entry_of(kind_timed, static_cast<std::uint32_t>(t.bytes.size())));
        t.bytes.insert(t.bytes.end(), inner.bytes.begin(), inner.bytes.end());
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a timed as T") {
        table_builder t = attr({{kind_int, 16}}, {}, 0);
        const table_builder inner = timed(kind_int, 8);
        t.patch(20, entry_of(kind_timed, static_cast<std::uint32_t>(t.bytes.size())));
        t.bytes.insert(t.bytes.end(), inner.bytes.begin(), inner.bytes.end());
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("an attr as a member of a record") {
        table_builder t;
        t.head(kind_record, 1, 1192);
        t.entry(0, kind_attr, 24);
        t.name("a");
        t.pad();
        const table_builder inner = int_request();
        t.bytes.insert(t.bytes.end(), inner.bytes.begin(), inner.bytes.end());
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("T at 1176 instead of 1184") {
        table_builder t = int_request();
        t.patch(16, 1176);
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("an argument that does not start at 48") {
        table_builder t = int_request();
        t.patch(24, 56);
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a head size that is not the layout size") {
        table_builder t = int_request();
        t.patch(4, 1200);
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a nonzero byte after the argument count") {
        table_builder t = int_request();
        t.bytes[10] = std::byte{1};
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a flags byte on another kind") {
        table_builder t;
        t.head(kind_record, 1, 8, 1);
        t.entry(0, kind_uint8, 1);
        t.name("a");
        t.pad();
        type_table other;
        const std::uint32_t entry = entry_of(kind_record, 0);
        CHECK(other.parse(t.span(), {&entry, 1}, true) == status::corrupt);
    }
    SUBCASE("an item of a stream") {
        const table_builder t = int_request();
        const std::string name = unique("attr-item");
        const auto made = stream::create(name, t.span(), entry_of(kind_attr, 0), 4, 1, 1);
        CHECK((!made && made.error().code == status::range));
    }
}

TEST_CASE("attr_publish writes the parts it names") {
    const std::string name = unique("attr-publish");
    handle box = make(name, everything());
    const std::uint64_t generation = box.generation();
    const std::uint16_t slot = *box.register_waiter();

    const std::int64_t before = detail::wall_ns();
    auto woken = std::async(std::launch::async, [&] { return box.wait(slot, generation, patience); });
    REQUIRE(box.attr_publish(0, publish_value, parts(0, 5, 111, 222), patience).has_value());
    const std::int64_t after = detail::wall_ns();
    const auto waited = woken.get();
    REQUIRE(waited.has_value());
    CHECK(*waited == wake::changed);

    auto now = value_part(box);
    CHECK((at(now, 0) == 1 && at(now, 1) == 5 && at(now, 3) == 0));
    CHECK((at(now, 2) >= before && at(now, 2) <= after));
    CHECK((box.version(0) == 1 && box.generation() == generation + 1));

    REQUIRE(box.attr_publish(0, publish_value | publish_timestamp, parts(0, 5, 99, 222), patience).has_value());
    now = value_part(box);
    CHECK((at(now, 0) == 2 && at(now, 1) == 5 && at(now, 2) == 99 && at(now, 3) == 0 && box.version(0) == 2));

    REQUIRE(box.attr_publish(0, publish_setpoint, parts(0, 1000, 2000, 7), patience).has_value());
    now = value_part(box);
    CHECK((at(now, 0) == 3 && at(now, 1) == 5 && at(now, 2) == 99 && at(now, 3) == 7 && box.version(0) == 3));

    REQUIRE(box.attr_publish(0, 7, parts(0, 8, 9, 10), patience).has_value());
    now = value_part(box);
    CHECK((at(now, 0) == 4 && at(now, 1) == 8 && at(now, 2) == 9 && at(now, 3) == 10 && box.version(0) == 4));
    box.release_waiter(slot);
    static_cast<void>(unlink(name));
}

TEST_CASE("attr_publish refuses a part the field lacks") {
    const auto refuses = [](const table_builder &t, std::uint16_t field, std::uint8_t named, std::size_t length) {
        const std::string name = unique("attr-refused");
        handle box = make(name, t);
        std::vector<std::byte> room(box.field(0).capacity);
        REQUIRE(box.read(0, room).has_value());
        const std::uint64_t seq = seq_of(box);
        const std::vector<std::byte> bytes(length);

        const auto refused = box.attr_publish(field, named, bytes, patience);
        CHECK((!refused && refused.error().code == status::range));

        std::vector<std::byte> after(room.size());
        REQUIRE(box.read(0, after).has_value());
        CHECK((after == room && seq_of(box) == seq && box.version(0) == 0));
        static_cast<void>(unlink(name));
    };
    SUBCASE("parts 0") { refuses(everything(), 0, 0, everything_parts); }
    SUBCASE("a timestamp alone") { refuses(everything(), 0, publish_timestamp, everything_parts); }
    SUBCASE("a timestamp on an attr that is not timed") {
        refuses(attr({{kind_int, 8}}, {}, 0), 0, publish_value | publish_timestamp, 16);
    }
    SUBCASE("a setpoint on an attr that is not settable") {
        refuses(attr({{kind_int, 8}}, {}, attr_timed), 0, publish_setpoint, 24);
    }
    SUBCASE("a value on an attr without one") {
        refuses(attr(std::nullopt, {}, attr_request), 0, publish_value, 8);
    }
    SUBCASE("a field past the last") { refuses(everything(), 1, publish_value, everything_parts); }
    SUBCASE("bytes one byte short") { refuses(everything(), 0, publish_value, everything_parts - 1); }
    SUBCASE("a field of another kind") {
        const std::string name = unique("attr-int");
        const field_spec fields[1] = {{0, 8, kind_int}};
        auto box = handle::create(name, fields, 8, 1, 4, {}, {});
        REQUIRE(box.has_value());
        const std::array<std::byte, 8> bytes{};
        const auto refused = box->attr_publish(0, publish_value, bytes, patience);
        CHECK((!refused && refused.error().code == status::range));
        CHECK(box->version(0) == 0);
        static_cast<void>(unlink(name));
    }
}

TEST_CASE("attr_publish stamps a timestamp that is the timed mark") {
    const std::string name = unique("attr-mark");
    handle box = make(name, everything());
    const std::int64_t before = detail::wall_ns();
    REQUIRE(box.attr_publish(0, publish_value | publish_timestamp,
                             parts(0, 5, std::numeric_limits<std::int64_t>::min(), 0), patience)
                .has_value());
    const std::int64_t after = detail::wall_ns();
    const auto now = value_part(box);
    CHECK((at(now, 2) >= before && at(now, 2) <= after));
    static_cast<void>(unlink(name));
}

TEST_CASE("a request step leaves the value part") {
    const std::string name = unique("attr-request-value");
    handle box = make(name, everything());
    REQUIRE(box.attr_publish(0, publish_value | publish_setpoint, parts(0, 5, 0, 7), patience).has_value());
    const auto published = value_part(box);
    const std::uint64_t version = box.version(0);

    std::array<std::byte, 8> arguments{};
    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    REQUIRE(box.request_take(0, arguments, patience).has_value());
    REQUIRE(box.request_answer(0, 1, true, eight(2), patience).has_value());
    CHECK((value_part(box) == published && box.version(0) == version + 3));
    static_cast<void>(unlink(name));
}

TEST_CASE("attr_published counts publishes and not request steps") {
    const std::string name = unique("attr-published");
    handle box = make(name, everything());
    CHECK(*box.attr_published(0) == 0);
    REQUIRE(box.attr_publish(0, publish_value, parts(0, 5, 0, 0), patience).has_value());
    REQUIRE(box.attr_publish(0, publish_setpoint, parts(0, 0, 0, 7), patience).has_value());
    std::array<std::byte, 8> arguments{};
    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    REQUIRE(box.request_take(0, arguments, patience).has_value());
    CHECK((*box.attr_published(0) == 2 && box.version(0) == 4));
    CHECK(box.attr_published(1).error().code == status::range);
    static_cast<void>(unlink(name));

    const std::string bare = unique("attr-published-bare");
    handle request_only = make(bare, attr(std::nullopt, {}, attr_request));
    CHECK(request_only.attr_published(0).error().code == status::range);
    static_cast<void>(unlink(bare));
}

TEST_CASE("attr_read copies the value part only") {
    const std::string name = unique("attr-read");
    handle box = make(name, everything());
    std::array<std::byte, everything_parts - 1> short_out{};
    const auto refused = box.attr_read(0, short_out);
    CHECK((!refused && refused.error().code == status::range));
    std::array<std::byte, everything_parts + 1> long_out{};
    CHECK(box.attr_read(0, long_out).error().code == status::range);
    static_cast<void>(unlink(name));

    const std::string bare = unique("attr-read-bare");
    handle request_only = make(bare, attr(std::nullopt, {}, attr_request));
    std::array<std::byte, 8> out{};
    CHECK(request_only.attr_read(0, out).error().code == status::range);
    static_cast<void>(unlink(bare));
}

TEST_CASE("a request is answered") {
    const std::string name = unique("attr-answered");
    handle box = make(name, int_request());
    const std::uint64_t generation = box.generation();

    const auto sent = box.request_send(0, eight(5), patience);
    REQUIRE(sent.has_value());
    CHECK(*sent == 1);
    std::array<std::byte, 8> arguments{};
    const auto taken = box.request_take(0, arguments, patience);
    REQUIRE(taken.has_value());
    CHECK((*taken == 1 && number_of(arguments) == 5));
    CHECK(box.request_answer(0, 1, true, eight(7), patience).has_value());

    std::array<std::byte, 256> out{};
    const auto reply = box.request_answer_of(0, 1, out, false);
    REQUIRE(reply.has_value());
    CHECK((reply->outcome == request_outcome::answered && reply->length == 8 && number_of(out) == 7));
    CHECK((box.version(0) == 3 && box.generation() == generation + 3));
    static_cast<void>(unlink(name));
}

TEST_CASE("a step that changes nothing leaves the sequence number and the write count") {
    const std::string name = unique("attr-unchanged");
    handle box = make(name, int_request());
    std::array<std::byte, 8> arguments{};
    const std::uint64_t seq = seq_of(box);

    const auto none = box.request_take(0, arguments, patience);
    REQUIRE(none.has_value());
    CHECK(*none == 0);
    CHECK((seq_of(box) == seq && box.version(0) == 0));

    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    const std::uint64_t after_send = seq_of(box);
    const auto early = box.request_answer(0, 1, true, eight(1), patience);
    CHECK((!early && early.error().code == status::ended));
    CHECK((seq_of(box) == after_send && box.version(0) == 1));
    static_cast<void>(unlink(name));
}

TEST_CASE("a request replaced while no server takes any reads superseded at once") {
    const std::string name = unique("attr-superseded-unserved");
    handle box = make(name, int_request());
    std::array<std::byte, 256> out{};

    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    REQUIRE(box.request_send(0, eight(2), patience).has_value());
    CHECK(box.request_answer_of(0, 1, out, false)->outcome == request_outcome::superseded);
    CHECK(box.request_answer_of(0, 2, out, false)->outcome == request_outcome::pending);
    static_cast<void>(unlink(name));
}

TEST_CASE("a later request supersedes one the server never took") {
    const std::string name = unique("attr-superseded");
    handle box = make(name, int_request());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    CHECK(*box.request_take(0, arguments, patience) == 1);
    REQUIRE(box.request_send(0, eight(2), patience).has_value());
    REQUIRE(box.request_send(0, eight(3), patience).has_value());
    CHECK(box.request_answer(0, 1, true, eight(10), patience).has_value());
    const auto third = box.request_take(0, arguments, patience);
    REQUIRE(third.has_value());
    CHECK((*third == 3 && number_of(arguments) == 3));

    CHECK(box.request_answer_of(0, 2, out, false)->outcome == request_outcome::superseded);
    CHECK(box.request_answer_of(0, 3, out, false)->outcome == request_outcome::pending);
    CHECK(box.request_answer(0, 3, true, eight(30), patience).has_value());
    const auto reply = box.request_answer_of(0, 3, out, false);
    CHECK((reply->outcome == request_outcome::answered && number_of(out) == 30));
    static_cast<void>(unlink(name));
}

TEST_CASE("an answer four later answers replaced is overwritten") {
    const std::string name = unique("attr-overwritten");
    handle box = make(name, int_request());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    for (std::int64_t n = 1; n <= 5; ++n) {
        REQUIRE(box.request_send(0, eight(n), patience).has_value());
        REQUIRE(box.request_take(0, arguments, patience).has_value());
        REQUIRE(box.request_answer(0, static_cast<std::uint64_t>(n), true, eight(n * 10), patience).has_value());
    }
    CHECK(box.request_answer_of(0, 1, out, false)->outcome == request_outcome::overwritten);
    const auto second = box.request_answer_of(0, 2, out, false);
    CHECK((second->outcome == request_outcome::answered && number_of(out) == 20));
    static_cast<void>(unlink(name));
}

TEST_CASE("a request nobody serves stays pending") {
    const std::string name = unique("attr-pending");
    handle box = make(name, int_request());
    std::array<std::byte, 256> out{};

    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    CHECK(box.request_answer_of(0, 1, out, true)->outcome == request_outcome::pending);
    CHECK(box.request_answer_of(0, 2, out, true).error().code == status::range);
    CHECK(box.request_answer_of(0, 0, out, true).error().code == status::range);
    std::array<std::byte, 255> small{};
    CHECK(box.request_answer_of(0, 1, small, true).error().code == status::range);
    static_cast<void>(unlink(name));
}

TEST_CASE("a claim is refused while its server runs and allowed after it exited") {
    const std::string name = unique("attr-claim");
    handle box = make(name, int_request());
    auto other = handle::open(name, patience);
    REQUIRE(other.has_value());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    CHECK(box.serve_claim(0, patience).has_value());
    const std::uint64_t claimed = box.version(0);
    const auto refused = other->serve_claim(0, patience);
    CHECK((!refused && refused.error().code == status::busy));
    CHECK(box.version(0) == claimed);

    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    REQUIRE(box.request_take(0, arguments, patience).has_value());
    std::vector<std::byte> record(box.field(0).capacity);
    REQUIRE(box.read(0, record).has_value());
    const std::uint64_t other_start = detail::current_start() + 1;
    std::memcpy(record.data() + server_start_at, &other_start, sizeof other_start);
    const value forged[1] = {{0, record}};
    REQUIRE(box.write(forged, patience).has_value());

    CHECK(box.request_answer_of(0, 1, out, true)->outcome == request_outcome::server_exited);
    CHECK(other->serve_claim(0, patience).has_value());
    const auto reply = other->request_answer_of(0, 1, out, true);
    REQUIRE(reply.has_value());
    CHECK(reply->outcome == request_outcome::failed);
    CHECK(text_of(out, reply->length) == "the server exited");
    static_cast<void>(unlink(name));
}

TEST_CASE("release answers the request its server took") {
    const std::string name = unique("attr-release");
    handle box = make(name, int_request());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    REQUIRE(box.serve_claim(0, patience).has_value());
    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    REQUIRE(box.request_take(0, arguments, patience).has_value());
    CHECK(box.serve_release(0, patience).has_value());

    const auto reply = box.request_answer_of(0, 1, out, true);
    REQUIRE(reply.has_value());
    CHECK(reply->outcome == request_outcome::failed);
    CHECK(text_of(out, reply->length) == "the server stopped");
    const auto late = box.request_answer(0, 1, true, eight(1), patience);
    CHECK((!late && late.error().code == status::ended));

    const std::uint64_t seq = seq_of(box);
    const std::uint64_t version = box.version(0);
    CHECK(box.serve_release(0, patience).has_value());
    CHECK((seq_of(box) == seq && box.version(0) == version));
    static_cast<void>(unlink(name));
}

TEST_CASE("an answer for a request not taken is refused") {
    const std::string name = unique("attr-not-taken");
    handle box = make(name, int_request());

    REQUIRE(box.request_send(0, eight(1), patience).has_value());
    const auto early = box.request_answer(0, 1, true, eight(1), patience);
    CHECK((!early && early.error().code == status::ended));
    const std::array<std::byte, 257> long_text{};
    const auto long_error = box.request_answer(0, 1, false, long_text, patience);
    CHECK((!long_error && long_error.error().code == status::range));
    static_cast<void>(unlink(name));
}

TEST_CASE("large attr copies run the wait hooks once around themselves") {
    // bytes of 1 MiB: the value part is a little over the large copy size.
    const std::string name = unique("attr-large");
    handle box = make(name, attr({{kind_bytes, max_capacity}}, {}, attr_timed));
    auto other = handle::open(name, patience);
    REQUIRE(other.has_value());
    count_hooks(box);
    const std::size_t size = box.field(0).capacity;
    const std::size_t value_end = 8 + 4 + max_capacity;
    const std::size_t stamp_at = 8 + detail::round_up(4 + max_capacity, 8);
    REQUIRE(size >= detail::large_copy);
    std::vector<std::byte> bytes(size);
    for (std::size_t i = 8; i < size; ++i)
        bytes[i] = static_cast<std::byte>(i * 7 + 1);

    const auto contended = [&](auto &&call) {
        hooks_before = hooks_after = 0;
        const auto locked = other->lock(patience);
        REQUIRE(locked.has_value());
        std::thread releaser([&] {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            other->unlock(*locked);
        });
        call();
        releaser.join();
        CHECK((hooks_before == 1 && hooks_after == 1));
    };

    const std::uint64_t generation = box.generation();
    contended(
        [&] { REQUIRE(box.attr_publish(0, publish_value | publish_timestamp, bytes, seconds(5.0)).has_value()); });
    CHECK((box.version(0) == 1 && box.generation() == generation + 2 && *box.attr_published(0) == 1));

    std::vector<std::byte> got(size);
    contended([&] { REQUIRE(box.attr_read(0, got).has_value()); });
    CHECK(std::equal(bytes.begin() + 8, bytes.begin() + value_end, got.begin() + 8));
    CHECK(std::equal(bytes.begin() + stamp_at, bytes.begin() + stamp_at + 8, got.begin() + stamp_at));
    CHECK(number_of(got) == 1);

    hooks_before = hooks_after = 0;
    REQUIRE(box.attr_publish(0, publish_value, bytes, patience).has_value());
    CHECK((hooks_before == 1 && hooks_after == 1));
    hooks_before = hooks_after = 0;
    REQUIRE(box.attr_read(0, got).has_value());
    CHECK((hooks_before == 1 && hooks_after == 1 && number_of(got) == 2));
    static_cast<void>(unlink(name));
}

TEST_CASE("a small attr copy does not run the wait hooks") {
    const std::string name = unique("attr-small-hooks");
    handle box = make(name, everything());
    count_hooks(box);
    REQUIRE(box.attr_publish(0, publish_value, parts(0, 5, 0, 0), patience).has_value());
    static_cast<void>(value_part(box));
    CHECK((hooks_before == 0 && hooks_after == 0));
    static_cast<void>(unlink(name));
}

TEST_CASE("a type_view of an attr gives the flags and the offsets of its parts") {
    struct shape {
        table_builder table;
        std::uint8_t flags;
        std::uint32_t value, timestamp, setpoint;
    };
    const std::vector<shape> shapes = {
        {attr({{kind_int, 8}}, {}, 0), 0, 8, 0, 0},
        {attr({{kind_int, 8}}, {}, attr_timed), attr_timed, 8, 16, 0},
        {int_request(), attr_request, 1184, 0, 0},
        {attr({{kind_int, 8}}, {{kind_int, 8}}, attr_settable | attr_request), attr_settable | attr_request, 1184,
         0, 1192},
        {everything(), 7, 1184, 1192, 1200},
        {attr(std::nullopt, {}, attr_request), attr_request, 0, 0, 0},
    };
    for (const shape &s : shapes) {
        INFO("flags ", int{s.flags});
        const std::string name = unique("attr-view");
        handle box = make(name, s.table);
        const type_view view = box.field_type(0);
        CHECK((view.attr_flags() == s.flags && view.value_offset() == s.value &&
               view.timestamp_offset() == s.timestamp && view.setpoint_offset() == s.setpoint));
        static_cast<void>(unlink(name));
    }

    const std::string name = unique("attr-view-int");
    const field_spec fields[1] = {{0, 8, kind_int}};
    auto box = handle::create(name, fields, 8, 1, 4, {}, {});
    REQUIRE(box.has_value());
    const type_view view = box->field_type(0);
    CHECK((view.attr_flags() == 0 && view.value_offset() == 0 && view.timestamp_offset() == 0 &&
           view.setpoint_offset() == 0));
    static_cast<void>(unlink(name));
}
