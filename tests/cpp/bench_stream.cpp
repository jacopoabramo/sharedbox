// Items per second through a stream in one process: a sender thread and 1 or 4 reader threads in each
// mode, for 1 KiB items and 512 x 512 uint16 frames. Not a test; built only on request.
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

using namespace sharedbox;

namespace {

table_builder bytes_array(std::uint64_t n) {
    table_builder t;
    t.head(kind_array, 0, static_cast<std::uint32_t>(n));
    t.put(dl_dtype{1, 8, 1});
    t.put(std::uint8_t{1});
    t.put(std::uint8_t{0});
    t.put(std::uint16_t{0});
    t.put(n);
    t.pad();
    return t;
}

const char *mode_name(read_mode mode) {
    return mode == read_mode::lossless ? "lossless" : mode == read_mode::lossy ? "lossy" : "latest";
}

double items_per_second(std::uint64_t bytes, read_mode mode, int readers, std::uint64_t items) {
    const std::string name =
        unique("bench") + "-" + std::to_string(bytes) + "-" + mode_name(mode) + "-" + std::to_string(readers);
    const table_builder t = bytes_array(bytes);
    auto st = stream::create(name, t.span(), entry_of(kind_array, 0), 32, 8, 1);
    if (!st) {
        std::fprintf(stderr, "create failed: %d\n", static_cast<int>(st.error().code));
        std::exit(1);
    }
    std::vector<stream_reader> opened;
    for (int i = 0; i < readers; ++i)
        opened.push_back(std::move(*st->reader(mode, start_at::oldest)));
    std::vector<std::thread> pool;
    for (stream_reader &r : opened)
        pool.emplace_back([&r, bytes] {
            std::vector<std::byte> buf(bytes);
            while (r.receive(buf, seconds(5.0)))
                ;
        });
    auto sender = st->sender();
    std::vector<std::byte> item(bytes, std::byte{1});
    const auto begin = std::chrono::steady_clock::now();
    for (std::uint64_t i = 0; i < items; ++i)
        if (!sender->send(item, seconds(5.0))) {
            std::fprintf(stderr, "send failed\n");
            std::exit(1);
        }
    sender->close();
    for (std::thread &th : pool)
        th.join();
    const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - begin).count();
    opened.clear();
    static_cast<void>(unlink(name));
    return static_cast<double>(items) / elapsed;
}

} // namespace

int main() {
    for (const std::uint64_t bytes : {std::uint64_t{1024}, std::uint64_t{512 * 512 * 2}})
        for (const read_mode mode : {read_mode::lossless, read_mode::lossy, read_mode::latest})
            for (const int readers : {1, 4}) {
                const std::uint64_t items = bytes == 1024 ? 1000000 : 5000;
                std::printf("%7llu B  %-8s  %d reader(s)  %12.0f items/s\n",
                            static_cast<unsigned long long>(bytes), mode_name(mode), readers,
                            items_per_second(bytes, mode, readers, items));
            }
    return 0;
}
