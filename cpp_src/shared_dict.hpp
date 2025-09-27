#pragma once

#include <boost/interprocess/managed_shared_memory.hpp>
#include <boost/interprocess/sync/interprocess_mutex.hpp>
#include <boost/container/vector.hpp>
#include <boost/container/map.hpp>
#include <cstring>
#include <string>
#include <vector>
#include <memory>

namespace bipc = boost::interprocess;

namespace shared_memory {

// Type aliases and definitions moved outside class
using segment_t = bipc::managed_shared_memory;
using segment_manager_t = segment_t::segment_manager;

template <class T>
using ShmemAlloc = bipc::allocator<T, segment_manager_t>;

using ByteVec = boost::container::vector<char, ShmemAlloc<char>>;

struct KeyLess {
    bool operator()(const ByteVec& a, const ByteVec& b) const noexcept {
        const std::size_t as = a.size();
        const std::size_t bs = b.size();
        const std::size_t n = as < bs ? as : bs;
        int cmp = n ? std::memcmp(a.data(), b.data(), n) : 0;
        if (cmp != 0) return cmp < 0;
        return as < bs;
    }
};

using MapValueType = std::pair<const ByteVec, ByteVec>;
using MapAlloc = ShmemAlloc<MapValueType>;
using Map = boost::container::map<ByteVec, ByteVec, KeyLess, MapAlloc>;
using Mutex = bipc::interprocess_mutex;

class SharedMemoryDict {
public:
    SharedMemoryDict(const std::string& name, std::size_t size, bool create, std::size_t max_keys = 128);
    ~SharedMemoryDict();

    void set(const std::string& key_bytes, const std::string& value_bytes);
    bool get(const std::string& key_bytes, std::string& out_value_bytes) const;
    bool erase(const std::string& key_bytes);
    bool contains(const std::string& key_bytes) const;
    std::size_t size() const;
    std::vector<std::string> keys() const;

    void close();    // Close access to shared memory without removing it
    void unlink();   // Remove shared memory segment
    bool is_closed() const;  // Check if the connection has been closed

private:

    static ByteVec make_bytevec(const std::string& s, segment_manager_t* mgr);
    static std::size_t hash_bytes(const ByteVec& key) noexcept;
    std::size_t get_key_index(const ByteVec& key) const;
    Mutex& get_mutex_for_key(const ByteVec& key) const;
    void check_not_closed() const;

    std::string name_;
    std::size_t max_keys_;
    bool is_closed_;

    segment_t segment_;
    Map* map_;
    Mutex* mutexes_;
};

} // namespace shared_memory
