#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>
#include <cstdint>
#include <cstring>
#include <vector>

#include "_core/sharedmemory.hpp"

namespace nb = nanobind;
using namespace shared_memory;

constexpr size_t DEFAULT_SIZE = 128 * 1024 * 1024; // 128 MB
constexpr size_t DEFAULT_MAX_KEYS = 128; // Default max keys if not specified
constexpr uint8_t PICKLE_MARKER = 0x00; // Marker byte for pickle-serialized data
constexpr uint8_t NUMPY_MARKER = 0x01; // Marker byte for numpy-serialized data

// Native numpy array header for efficient serialization
// Layout: [marker(1)] [dtype_len(4)] [ndim(4)] [shape[0]..shape[n](8*n)] [data_len(8)] [data]
struct NumpyHeader {
    uint32_t dtype_len;
    uint32_t ndim;
    std::vector<uint64_t> shape;
    uint64_t data_len;
    std::string dtype_str;
};

class SharedDict {
public:
    SharedDict(
        const std::string &name, 
        nb::object data = nb::none(),
        size_t size = DEFAULT_SIZE,
        bool create = true,
        size_t max_keys = DEFAULT_MAX_KEYS
    );
    ~SharedDict();

    // Lifecycle management
    void close();
    void unlink();
    bool is_closed() const;

    // Python dict-like interface (using nanobind protocols)
    size_t __len__() const;
    bool __contains__(const std::string &key) const;
    nb::object __getitem__(const std::string &key) const;
    void __setitem__(const std::string &key, const nb::object &value);
    void __delitem__(const std::string &key);
    nb::object get(const std::string &key, const nb::object &default_value = nb::none()) const;
    
    // Python iteration support
    nb::list keys() const;
    nb::list values() const;
    nb::list items() const;

    // Statistics and sizing (implemented via Python utils module)
    nb::dict get_stats() const;
    nb::dict recommend_sizing(nb::object target_entries = nb::none()) const;

private:
    std::string name_;
    size_t size_;
    bool created_;
    size_t max_keys_;
    SharedMemoryDict* shm_ptr_;
    
    // Python pickle module for generic object serialization
    nb::object pickle_module_;

    // Core serialization methods
    std::string serialize_value(const nb::object &obj) const;
    nb::object deserialize_value(const std::string &data) const;
    
    // Native numpy array serialization (no pickle overhead)
    std::string serialize_numpy(const nb::ndarray<> &arr) const;
    nb::object deserialize_numpy(const char* data, size_t size) const;
    
    // Helper to check if object is numpy array
    bool is_numpy_array(const nb::object &obj) const;
    
    // Initialization helper
    void initialize_data(const nb::object &data);
};