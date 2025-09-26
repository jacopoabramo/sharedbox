#include "shared_dict.hpp"
#include <boost/interprocess/shared_memory_object.hpp>
#include <stdexcept>
#include <thread>
#include <chrono>

namespace shared_memory {

ByteVec SharedMemoryDict::make_bytevec(const std::string& s, segment_manager_t* mgr) {
    ShmemAlloc<char> alloc(mgr);
    ByteVec v(alloc);
    v.resize(s.size());
    if (!s.empty()) std::memcpy(v.data(), s.data(), s.size());
    return v;
}

SharedMemoryDict::SharedMemoryDict(const std::string& name, std::size_t size, bool create, std::size_t max_keys)
    : name_(name),
      max_keys_(max_keys),
      is_closed_(false),
      segment_(create ? segment_t(boost::interprocess::open_or_create, name.c_str(), size)
                      : segment_t(boost::interprocess::open_only, name.c_str()))
{
    auto* mgr = segment_.get_segment_manager();

    // construct/find data map
    map_ = segment_.find_or_construct<Map>("__map")(KeyLess(), MapAlloc(mgr));
    
    // construct/find mutex array - one mutex per key slot
    std::pair<Mutex*, std::size_t> mutexes_info = segment_.find<Mutex>("__mutexes");
    if (mutexes_info.first == nullptr) {
        // First time - construct array of mutexes
        mutexes_ = segment_.construct<Mutex>("__mutexes")[max_keys_]();
    } else {
        // Already exists - use existing array
        mutexes_ = mutexes_info.first;
        // Verify the existing array has enough slots
        if (mutexes_info.second < max_keys_) {
            throw std::runtime_error("Existing shared memory has fewer mutex slots than requested");
        }
    }
}

SharedMemoryDict::~SharedMemoryDict() {
    // Only close if not already closed
    // The owner of the shared memory
    // will call "unlink()" to free resources;
    // children just call close()
    if (!is_closed_) {
        is_closed_ = true;  // Mark as closed to prevent further operations
    }
}

void SharedMemoryDict::check_not_closed() const {
    if (is_closed_) {
        throw std::runtime_error("SharedMemoryDict has been closed and cannot be used");
    }
}

std::size_t SharedMemoryDict::hash_bytes(const ByteVec& key) noexcept {
    // 64-bit FNV-1a hash
    const std::size_t fnv_offset = 1469598103934665603ull;
    const std::size_t fnv_prime  = 1099511628211ull;
    std::size_t h = fnv_offset;
    for (std::size_t i = 0; i < key.size(); ++i) {
        h ^= static_cast<unsigned char>(key[i]);
        h *= fnv_prime;
    }
    return h;
}

std::size_t SharedMemoryDict::get_key_index(const ByteVec& key) const {
    std::size_t h = hash_bytes(key);
    return h % max_keys_;
}

Mutex& SharedMemoryDict::get_mutex_for_key(const ByteVec& key) const {
    std::size_t index = get_key_index(key);
    return mutexes_[index];
}


void SharedMemoryDict::set(const std::string& key_bytes, const std::string& value_bytes) {
    check_not_closed();
    auto* mgr = segment_.get_segment_manager();
    ByteVec k = make_bytevec(key_bytes, mgr);
    
    Mutex& key_mutex = get_mutex_for_key(k);
    key_mutex.lock();
    try {
        auto it = map_->find(k);
        if (it == map_->end()) {
            ByteVec v = make_bytevec(value_bytes, mgr);
            map_->emplace(std::move(k), std::move(v));
        } else {
            ByteVec v = make_bytevec(value_bytes, mgr);
            it->second.swap(v);
        }
        key_mutex.unlock();
    } catch (...) {
        key_mutex.unlock();
        throw;
    }
}

bool SharedMemoryDict::get(const std::string& key_bytes, std::string& out_value_bytes) const {
    check_not_closed();
    auto* mgr = segment_.get_segment_manager();
    ByteVec k = make_bytevec(key_bytes, mgr);
    
    Mutex& key_mutex = get_mutex_for_key(k);
    key_mutex.lock();
    try {
        auto it = map_->find(k);
        if (it != map_->end()) {
            const auto& v = it->second;
            out_value_bytes.resize(v.size());
            if (!v.empty()) std::memcpy(out_value_bytes.data(), v.data(), v.size());
            key_mutex.unlock();
            return true;
        }
        key_mutex.unlock();
    } catch (...) {
        key_mutex.unlock();
        throw;
    }
    return false;
}

bool SharedMemoryDict::erase(const std::string& key_bytes) {
    check_not_closed();
    auto* mgr = segment_.get_segment_manager();
    ByteVec k = make_bytevec(key_bytes, mgr);
    
    Mutex& key_mutex = get_mutex_for_key(k);
    key_mutex.lock();
    try {
        bool erased = (map_->erase(k) > 0);
        key_mutex.unlock();
        return erased;
    } catch (...) {
        key_mutex.unlock();
        throw;
    }
}

bool SharedMemoryDict::contains(const std::string& key_bytes) const {
    check_not_closed();
    auto* mgr = segment_.get_segment_manager();
    ByteVec k = make_bytevec(key_bytes, mgr);
    
    Mutex& key_mutex = get_mutex_for_key(k);
    key_mutex.lock();
    try {
        bool found = (map_->find(k) != map_->end());
        key_mutex.unlock();
        return found;
    } catch (...) {
        key_mutex.unlock();
        throw;
    }
}

std::size_t SharedMemoryDict::size() const {
    check_not_closed();
    return map_->size();
}

std::vector<std::string> SharedMemoryDict::keys() const {
    check_not_closed();
    std::vector<std::string> out;
    
    // Lock all mutexes in a consistent order to prevent deadlock
    // We'll lock all mutexes from index 0 to max_keys_-1
    std::vector<bool> locked(max_keys_, false);
    
    try {
        // Lock all mutexes in order
        for (std::size_t i = 0; i < max_keys_; ++i) {
            mutexes_[i].lock();
            locked[i] = true;
        }
        
        // Now we have exclusive access to all keys - safe to iterate
        out.reserve(map_->size());
        for (auto const& kv : *map_) {
            const auto& k = kv.first;
            std::string ks;
            ks.resize(k.size());
            if (!k.empty()) std::memcpy(ks.data(), k.data(), k.size());
            out.emplace_back(std::move(ks));
        }
        
        // Unlock all mutexes in reverse order
        for (int i = static_cast<int>(max_keys_) - 1; i >= 0; --i) {
            mutexes_[i].unlock();
            locked[i] = false;
        }
        
    } catch (...) {
        // Exception occurred - unlock any mutexes we managed to lock
        for (int i = static_cast<int>(max_keys_) - 1; i >= 0; --i) {
            if (locked[i]) {
                mutexes_[i].unlock();
            }
        }
        throw;
    }
    
    return out;
}



void SharedMemoryDict::close() {
    // Close access to shared memory without removing it
    // This makes the object unusable but doesn't delete the shared memory
    if (!is_closed_) {
        is_closed_ = true;
        // Note: We don't try to release locks here as that could cause issues
        // if this process is in the middle of an operation. The locks will be
        // released when the process terminates naturally.
    }
}

void SharedMemoryDict::unlink() {
    // Remove the shared memory segment entirely
    boost::interprocess::shared_memory_object::remove(name_.c_str());
}

bool SharedMemoryDict::is_closed() const {
    return is_closed_;
}

} // namespace shared_memory