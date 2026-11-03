// Call fields (kind 76): the description, and the protocol steps a client and a server take on a request.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>
#include <vector>

using namespace sharedbox;
using sharedbox::detail::type_table;

namespace {

constexpr seconds patience{1.0};
constexpr std::uint64_t server_start_at = 24;

status parse(const table_builder &t, type_table &table) {
    const std::uint32_t entry = entry_of(kind_call, 0);
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
    const field_spec fields[1] = {{0, 0, kind_call}};
    auto box = handle::create(name, fields, size, 1, 4, {}, t.span());
    REQUIRE(box.has_value());
    return std::move(*box);
}

table_builder int_call() { return call({{kind_int, 8}}, {{kind_int, 8}}); }

} // namespace

TEST_CASE("a call description parses as a box field only") {
    type_table table;
    REQUIRE(parse(int_call(), table) == status::ok);
    const detail::type_node &node = table.node(table.field(0).node);
    CHECK((table.field(0).kind == kind_call && table.field(0).size == 1176 && table.field(0).alignment == 8));
    CHECK((node.capacity == 1 && node.slots == 56 && node.stride == 280));
    CHECK((table.member(node.members).offset == 48 && table.member(node.members + 1).offset == 272));

    type_table bare;
    REQUIRE(parse(call({}, {}), bare) == status::ok);
    CHECK(bare.field(0).size == 1136);

    SUBCASE("a head size that is not the layout's") {
        table_builder t = int_call();
        t.patch(4, 1168);
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("an argument that does not start at 48") {
        table_builder t = int_call();
        t.patch(16, 56);
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a result that does not start at 272") {
        table_builder t = int_call();
        t.patch(24, 280);
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a results byte of 2") {
        table_builder t = int_call();
        t.bytes[10] = std::byte{2};
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("a call as a member of a record") {
        table_builder t;
        t.head(kind_record, 1, 1176);
        t.entry(0, kind_call, 24);
        t.name("a");
        t.pad();
        const table_builder inner = int_call();
        t.bytes.insert(t.bytes.end(), inner.bytes.begin(), inner.bytes.end());
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("an argument that is a timed") {
        table_builder t = call({{kind_int, 16}}, {});
        const table_builder inner = timed(kind_int, 8);
        t.patch(20, entry_of(kind_timed, static_cast<std::uint32_t>(t.bytes.size())));
        t.bytes.insert(t.bytes.end(), inner.bytes.begin(), inner.bytes.end());
        CHECK(parse(t) == status::corrupt);
    }
    SUBCASE("an item of a stream") {
        const table_builder t = int_call();
        const std::string name = unique("call-item");
        const auto made = stream::create(name, t.span(), entry_of(kind_call, 0), 4, 1, 1);
        CHECK((!made && made.error().code == status::range));
    }
}

TEST_CASE("a request is answered") {
    const std::string name = unique("call-answered");
    handle box = make(name, int_call());
    const std::uint64_t generation = box.generation();

    const auto sent = box.call_send(0, eight(5), patience);
    REQUIRE(sent.has_value());
    CHECK(*sent == 1);
    std::array<std::byte, 8> arguments{};
    const auto taken = box.call_take(0, arguments, patience);
    REQUIRE(taken.has_value());
    CHECK((*taken == 1 && number_of(arguments) == 5));
    CHECK(box.call_answer(0, 1, true, eight(7), patience).has_value());

    std::array<std::byte, 256> out{};
    const auto reply = box.call_answer_of(0, 1, out, false);
    REQUIRE(reply.has_value());
    CHECK((reply->outcome == call_outcome::answered && reply->length == 8 && number_of(out) == 7));
    CHECK((box.version(0) == 3 && box.generation() == generation + 3));
    static_cast<void>(unlink(name));
}

TEST_CASE("a step that changes nothing leaves the sequence number and the write count") {
    const std::string name = unique("call-unchanged");
    handle box = make(name, int_call());
    std::array<std::byte, 8> arguments{};
    const std::uint64_t seq = seq_of(box);

    const auto none = box.call_take(0, arguments, patience);
    REQUIRE(none.has_value());
    CHECK(*none == 0);
    CHECK((seq_of(box) == seq && box.version(0) == 0));

    REQUIRE(box.call_send(0, eight(1), patience).has_value());
    const std::uint64_t after_send = seq_of(box);
    const auto early = box.call_answer(0, 1, true, eight(1), patience);
    CHECK((!early && early.error().code == status::ended));
    CHECK((seq_of(box) == after_send && box.version(0) == 1));
    static_cast<void>(unlink(name));
}

TEST_CASE("a later request supersedes one the server never took") {
    const std::string name = unique("call-superseded");
    handle box = make(name, int_call());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    REQUIRE(box.call_send(0, eight(1), patience).has_value());
    CHECK(*box.call_take(0, arguments, patience) == 1);
    REQUIRE(box.call_send(0, eight(2), patience).has_value());
    REQUIRE(box.call_send(0, eight(3), patience).has_value());
    CHECK(box.call_answer(0, 1, true, eight(10), patience).has_value());
    const auto third = box.call_take(0, arguments, patience);
    REQUIRE(third.has_value());
    CHECK((*third == 3 && number_of(arguments) == 3));

    CHECK(box.call_answer_of(0, 2, out, false)->outcome == call_outcome::superseded);
    CHECK(box.call_answer_of(0, 3, out, false)->outcome == call_outcome::pending);
    CHECK(box.call_answer(0, 3, true, eight(30), patience).has_value());
    const auto reply = box.call_answer_of(0, 3, out, false);
    CHECK((reply->outcome == call_outcome::answered && number_of(out) == 30));
    static_cast<void>(unlink(name));
}

TEST_CASE("an answer four later answers replaced is overwritten") {
    const std::string name = unique("call-overwritten");
    handle box = make(name, int_call());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    for (std::int64_t n = 1; n <= 5; ++n) {
        REQUIRE(box.call_send(0, eight(n), patience).has_value());
        REQUIRE(box.call_take(0, arguments, patience).has_value());
        REQUIRE(box.call_answer(0, static_cast<std::uint64_t>(n), true, eight(n * 10), patience).has_value());
    }
    CHECK(box.call_answer_of(0, 1, out, false)->outcome == call_outcome::overwritten);
    const auto second = box.call_answer_of(0, 2, out, false);
    CHECK((second->outcome == call_outcome::answered && number_of(out) == 20));
    static_cast<void>(unlink(name));
}

TEST_CASE("a request nobody serves stays pending") {
    const std::string name = unique("call-pending");
    handle box = make(name, int_call());
    std::array<std::byte, 256> out{};

    REQUIRE(box.call_send(0, eight(1), patience).has_value());
    CHECK(box.call_answer_of(0, 1, out, true)->outcome == call_outcome::pending);
    CHECK(box.call_answer_of(0, 2, out, true).error().code == status::range);
    CHECK(box.call_answer_of(0, 0, out, true).error().code == status::range);
    std::array<std::byte, 255> small{};
    CHECK(box.call_answer_of(0, 1, small, true).error().code == status::range);
    static_cast<void>(unlink(name));
}

TEST_CASE("a claim is refused while its server runs and allowed after it exited") {
    const std::string name = unique("call-claim");
    handle box = make(name, int_call());
    auto other = handle::open(name, patience);
    REQUIRE(other.has_value());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    CHECK(box.call_claim(0, patience).has_value());
    const std::uint64_t claimed = box.version(0);
    const auto refused = other->call_claim(0, patience);
    CHECK((!refused && refused.error().code == status::busy));
    CHECK(box.version(0) == claimed);

    REQUIRE(box.call_send(0, eight(1), patience).has_value());
    REQUIRE(box.call_take(0, arguments, patience).has_value());
    std::vector<std::byte> record(box.field(0).capacity);
    REQUIRE(box.read(0, record).has_value());
    const std::uint64_t other_start = detail::current_start() + 1;
    std::memcpy(record.data() + server_start_at, &other_start, sizeof other_start);
    const value forged[1] = {{0, record}};
    REQUIRE(box.write(forged, patience).has_value());

    CHECK(box.call_answer_of(0, 1, out, true)->outcome == call_outcome::server_exited);
    CHECK(other->call_claim(0, patience).has_value());
    const auto reply = other->call_answer_of(0, 1, out, true);
    REQUIRE(reply.has_value());
    CHECK(reply->outcome == call_outcome::failed);
    CHECK(text_of(out, reply->length) == "the server exited");
    static_cast<void>(unlink(name));
}

TEST_CASE("release answers the request its server took") {
    const std::string name = unique("call-release");
    handle box = make(name, int_call());
    std::array<std::byte, 8> arguments{};
    std::array<std::byte, 256> out{};

    REQUIRE(box.call_claim(0, patience).has_value());
    REQUIRE(box.call_send(0, eight(1), patience).has_value());
    REQUIRE(box.call_take(0, arguments, patience).has_value());
    CHECK(box.call_release(0, patience).has_value());

    const auto reply = box.call_answer_of(0, 1, out, true);
    REQUIRE(reply.has_value());
    CHECK(reply->outcome == call_outcome::failed);
    CHECK(text_of(out, reply->length) == "the server stopped");
    const auto late = box.call_answer(0, 1, true, eight(1), patience);
    CHECK((!late && late.error().code == status::ended));

    const std::uint64_t seq = seq_of(box);
    const std::uint64_t version = box.version(0);
    CHECK(box.call_release(0, patience).has_value());
    CHECK((seq_of(box) == seq && box.version(0) == version));
    static_cast<void>(unlink(name));
}

TEST_CASE("an answer for a request not taken is refused") {
    const std::string name = unique("call-not-taken");
    handle box = make(name, int_call());

    REQUIRE(box.call_send(0, eight(1), patience).has_value());
    const auto early = box.call_answer(0, 1, true, eight(1), patience);
    CHECK((!early && early.error().code == status::ended));
    const std::array<std::byte, 257> long_text{};
    const auto long_error = box.call_answer(0, 1, false, long_text, patience);
    CHECK((!long_error && long_error.error().code == status::range));
    static_cast<void>(unlink(name));
}
