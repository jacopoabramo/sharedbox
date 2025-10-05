#include "shareddict.hpp"
#ifdef __linux__
#include <sstream>
#endif

// Helper to write multi-byte values in little-endian format
template <typename T>
static void write_le(std::string &buf, T value)
{
    for (size_t i = 0; i < sizeof(T); ++i)
    {
        buf.push_back(static_cast<char>((value >> (i * 8)) & 0xFF));
    }
}

// Helper to read multi-byte values in little-endian format
template <typename T>
static T read_le(const char *&ptr)
{
    T value = 0;
    for (size_t i = 0; i < sizeof(T); ++i)
    {
        value |= static_cast<T>(static_cast<uint8_t>(ptr[i])) << (i * 8);
    }
    ptr += sizeof(T);
    return value;
}

SharedDict::SharedDict(
    const std::string &name,
    nb::object data,
    const size_t size,
    const bool create,
    const size_t max_keys) : name_(name),
                             size_(size),
                             created_(create),
                             max_keys_(max_keys),
                             shm_ptr_(nullptr)
{
    pickle_module_ = nb::module_::import_("pickle");

    shm_ptr_ = new SharedMemoryDict(name_, size_, create, max_keys_);

    if (!data.is_none())
    {
        initialize_data(data);
    }
}

SharedDict::~SharedDict()
{
    if (shm_ptr_ != nullptr)
    {
        // The C++ destructor will call close() if not already closed
        delete shm_ptr_;
        shm_ptr_ = nullptr;
    }
}

void SharedDict::close()
{
    if (shm_ptr_ != nullptr)
    {
        shm_ptr_->close();
    }
}

void SharedDict::unlink()
{
    if (shm_ptr_ != nullptr)
    {
        if (!shm_ptr_->is_closed())
        {
            throw std::runtime_error("Cannot unlink a SharedDict that is still open. Call close() first.");
        }
        shm_ptr_->unlink();
    }
}

bool SharedDict::is_closed() const
{
    if (shm_ptr_ == nullptr)
    {
        return true;
    }
    return shm_ptr_->is_closed();
}

// Check if object is a numpy array (without importing numpy if not needed)
bool SharedDict::is_numpy_array(const nb::object &obj) const
{
    // Use nanobind's built-in check for ndarray
    return nb::isinstance<nb::ndarray<>>(obj);
}

// Serialize value: use native C++ for numpy, pickle for everything else
std::string SharedDict::serialize_value(const nb::object &obj) const
{
    if (is_numpy_array(obj))
    {
        // Native numpy serialization
        return serialize_numpy(nb::cast<nb::ndarray<>>(obj));
    }
    else
    {
        // Use pickle for Python objects
        std::string result;
        result.push_back(PICKLE_MARKER);

        // Call pickle.dumps with highest protocol
        nb::object pickled = pickle_module_.attr("dumps")(
            obj,
            nb::arg("protocol") = pickle_module_.attr("HIGHEST_PROTOCOL"));
        nb::bytes pickled_bytes = nb::cast<nb::bytes>(pickled);

        // Append pickled data
        const char *data = PyBytes_AsString(pickled_bytes.ptr());
        Py_ssize_t size = PyBytes_Size(pickled_bytes.ptr());
        result.append(data, size);

        return result;
    }
}

// Deserialize value: check marker to determine format
nb::object SharedDict::deserialize_value(const std::string &data) const
{
    if (data.empty())
    {
        throw std::runtime_error("Empty data cannot be deserialized");
    }

    uint8_t marker = static_cast<uint8_t>(data[0]);

    if (marker == NUMPY_MARKER)
    {
        // Native numpy deserialization
        return deserialize_numpy(data.data() + 1, data.size() - 1);
    }
    else if (marker == PICKLE_MARKER)
    {
        // Pickle deserialization (skip marker)
        nb::bytes pickled_data = nb::bytes(data.data() + 1, data.size() - 1);
        return pickle_module_.attr("loads")(pickled_data);
    }
    else
    {
        // Legacy data without marker - assume pickle
        nb::bytes pickled_data = nb::bytes(data.data(), data.size());
        return pickle_module_.attr("loads")(pickled_data);
    }
}

// Native numpy serialization - direct memory access, no pickle
std::string SharedDict::serialize_numpy(const nb::ndarray<> &arr) const
{
    std::string result;
    result.reserve(1024); // Reserve some space upfront

    // Write marker
    result.push_back(NUMPY_MARKER);

    // Get dtype information
    nb::dlpack::dtype dtype = arr.dtype();
    std::ostringstream dtype_stream;

    // Format dtype string manually based on dlpack dtype
    // Format: [<,>,|][type_code][itemsize]
    char endian = '<'; // little-endian (most common)
    char type_code;

    uint8_t code_value = static_cast<uint8_t>(dtype.code);
    if (code_value == static_cast<uint8_t>(nb::dlpack::dtype_code::Int))
    {
        type_code = 'i';
    }
    else if (code_value == static_cast<uint8_t>(nb::dlpack::dtype_code::UInt))
    {
        type_code = 'u';
    }
    else if (code_value == static_cast<uint8_t>(nb::dlpack::dtype_code::Float))
    {
        type_code = 'f';
    }
    else if (code_value == static_cast<uint8_t>(nb::dlpack::dtype_code::Complex))
    {
        type_code = 'c';
    }
    else if (code_value == static_cast<uint8_t>(nb::dlpack::dtype_code::Bool))
    {
        type_code = 'b';
    }
    else
    {
        throw std::runtime_error("Unsupported numpy dtype");
    }

    // Build dtype string (e.g., "<f8" for little-endian float64)
    dtype_stream << endian << type_code << (dtype.bits / 8);
    std::string dtype_str = dtype_stream.str();

    // Write dtype length and dtype string
    write_le<uint32_t>(result, static_cast<uint32_t>(dtype_str.size()));
    result.append(dtype_str);

    // Write ndim
    write_le<uint32_t>(result, static_cast<uint32_t>(arr.ndim()));

    // Write shape
    for (size_t i = 0; i < arr.ndim(); ++i)
    {
        write_le<uint64_t>(result, static_cast<uint64_t>(arr.shape(i)));
    }

    // Write data length
    size_t data_len = arr.nbytes();
    write_le<uint64_t>(result, static_cast<uint64_t>(data_len));

    // Write array data (direct memory copy)
    const char *data_ptr = static_cast<const char *>(arr.data());
    result.append(data_ptr, data_len);

    return result;
}

// Native numpy deserialization - reconstruct from raw bytes
nb::object SharedDict::deserialize_numpy(const char *data, size_t size) const
{
    const char *ptr = data;

    // Read dtype length and string
    uint32_t dtype_len = read_le<uint32_t>(ptr);
    std::string dtype_str(ptr, dtype_len);
    ptr += dtype_len;

    // Read ndim
    uint32_t ndim = read_le<uint32_t>(ptr);

    // Read shape
    std::vector<size_t> shape(ndim);
    for (uint32_t i = 0; i < ndim; ++i)
    {
        shape[i] = static_cast<size_t>(read_le<uint64_t>(ptr));
    }

    // Read data length
    uint64_t data_len = read_le<uint64_t>(ptr);

    // Get pointer to array data
    const void *array_data = ptr;

    // Import numpy module
    nb::object np = nb::module_::import_("numpy");

    // Create numpy array from buffer
    // Use frombuffer + reshape for proper numpy array
    nb::bytes data_bytes = nb::bytes(static_cast<const char *>(array_data), data_len);
    nb::object arr = np.attr("frombuffer")(data_bytes, nb::arg("dtype") = dtype_str);

    // Need to reshape if multi-dimensional
    if (ndim > 1)
    {
        nb::tuple shape_tuple = nb::make_tuple();
        for (size_t s : shape)
        {
            shape_tuple = nb::tuple(nb::tuple(shape_tuple) + nb::make_tuple(s));
        }
        arr = arr.attr("reshape")(shape_tuple);
    }

    // Return a copy to ensure proper memory ownership
    return np.attr("array")(arr, nb::arg("copy") = true);
}

void SharedDict::initialize_data(const nb::object &data)
{
    if (!nb::isinstance<nb::dict>(data))
    {
        throw nb::type_error("Argument 'data' has incorrect type (expected dict)");
    }

    nb::dict data_dict = nb::cast<nb::dict>(data);
    int initialized_count = 0;

    // Track if we had a type error to re-throw it
    bool had_type_error = false;
    std::string type_error_msg;

    try
    {
        for (auto item : data_dict)
        {
            nb::handle key_handle = item.first;
            nb::handle value_handle = item.second;

            if (!nb::isinstance<nb::str>(key_handle))
            {
                had_type_error = true;
                type_error_msg = "All keys must be strings";
                throw std::runtime_error(type_error_msg);
            }

            std::string key = nb::cast<std::string>(key_handle);
            nb::object value = nb::cast<nb::object>(value_handle);

            __setitem__(key, value);
            initialized_count++;
        }
    }
    catch (const std::exception &e)
    {
        // Check if it was a type error
        if (had_type_error)
        {
            throw nb::type_error(type_error_msg.c_str());
        }

        // Wrap other exceptions in ValueError
        std::string error_msg = "Failed to initialize SharedDict after " +
                                std::to_string(initialized_count) + " items: " + e.what();
        throw nb::value_error(error_msg.c_str());
    }
}

size_t SharedDict::__len__() const
{
    return shm_ptr_->size();
}

bool SharedDict::__contains__(const std::string &key) const
{
    return shm_ptr_->contains(key);
}

nb::object SharedDict::__getitem__(const std::string &key) const
{
    std::string value_data;
    if (!shm_ptr_->get(key, value_data))
    {
        throw nb::key_error(key.c_str());
    }
    return deserialize_value(value_data);
}

void SharedDict::__setitem__(const std::string &key, const nb::object &value)
{
    std::string value_data = serialize_value(value);
    shm_ptr_->set(key, value_data);
}

void SharedDict::__delitem__(const std::string &key)
{
    if (!shm_ptr_->erase(key))
    {
        throw nb::key_error(key.c_str());
    }
}

nb::object SharedDict::get(const std::string &key, const nb::object &default_value) const
{
    try
    {
        return __getitem__(key);
    }
    catch (const std::exception &)
    {
        return default_value;
    }
}

nb::list SharedDict::keys() const
{
    std::vector<std::string> key_vec = shm_ptr_->keys();
    nb::list result;
    for (const auto &key : key_vec)
    {
        result.append(key);
    }
    return result;
}

nb::list SharedDict::values() const
{
    nb::list result;
    for (const auto &key : shm_ptr_->keys())
    {
        result.append(__getitem__(key));
    }
    return result;
}

nb::list SharedDict::items() const
{
    nb::list result;
    for (const auto &key : shm_ptr_->keys())
    {
        nb::tuple item = nb::make_tuple(key, __getitem__(key));
        result.append(item);
    }
    return result;
}

nb::dict SharedDict::get_stats() const
{
    nb::dict stats;

    // Get sample of keys
    std::vector<std::string> all_keys = shm_ptr_->keys();
    size_t sample_size = std::min(all_keys.size(), size_t(100));

    size_t total_key_bytes = 0;
    size_t total_value_bytes = 0;

    for (size_t i = 0; i < sample_size; ++i)
    {
        const std::string &key = all_keys[i];
        total_key_bytes += key.size();

        std::string value_data;
        if (shm_ptr_->get(key, value_data))
        {
            total_value_bytes += value_data.size();
        }
    }

    double avg_key_bytes = sample_size > 0 ? static_cast<double>(total_key_bytes) / sample_size : 0.0;
    double avg_value_bytes = sample_size > 0 ? static_cast<double>(total_value_bytes) / sample_size : 0.0;

    stats["total_entries"] = static_cast<int>(shm_ptr_->size());
    stats["sample_size"] = static_cast<int>(sample_size);
    stats["avg_key_utf8_bytes"] = avg_key_bytes;
    stats["avg_value_pickle_bytes"] = avg_value_bytes;
    stats["estimated_data_bytes"] = static_cast<int>(shm_ptr_->size() * (avg_key_bytes + avg_value_bytes));
    stats["segment_name"] = name_;

    return stats;
}

nb::dict SharedDict::recommend_sizing(nb::object target_entries) const
{
    nb::dict result;

    // Get current stats
    nb::dict stats = get_stats();
    result["current_stats"] = stats;

    int current_entries = nb::cast<int>(stats["total_entries"]);
    int target;

    if (target_entries.is_none())
    {
        // Calculate target with 10x growth
        target = std::max(current_entries * 10, 10000);
    }
    else
    {
        target = nb::cast<int>(target_entries);
    }

    result["target_entries"] = target;

    if (current_entries == 0)
    {
        result["sizing_recommendation"] = nb::none();
        result["lock_recommendation"] = nb::none();
        result["message"] = "No data in SharedMemoryDict yet - cannot provide recommendations";
        return result;
    }

    // Import utils module for sizing calculations
    try
    {
        nb::object utils = nb::module_::import_("sharedbox.utils");

        double avg_key_bytes = nb::cast<double>(stats["avg_key_utf8_bytes"]);
        double avg_value_bytes = nb::cast<double>(stats["avg_value_pickle_bytes"]);

        // Call SegmentSizer.calculate_segment_size
        nb::object segment_sizer = utils.attr("SegmentSizer");
        nb::object sizing = segment_sizer.attr("calculate_segment_size")(
            target,
            static_cast<int>(avg_key_bytes),
            static_cast<int>(avg_value_bytes));
        result["sizing_recommendation"] = sizing;

        // Call LockTuner.recommend_lock_count
        nb::object lock_tuner = utils.attr("LockTuner");
        nb::object lock_rec = lock_tuner.attr("recommend_lock_count")(target);
        result["lock_recommendation"] = lock_rec;
    }
    catch (const std::exception &e)
    {
        result["sizing_recommendation"] = nb::none();
        result["lock_recommendation"] = nb::none();
        result["message"] = std::string("Could not calculate recommendations: ") + e.what();
    }

    return result;
}

// Nanobind module definition
NB_MODULE(_shareddict, m)
{
    m.doc() = "Native shared memory dictionary implementation using nanobind";

    nb::class_<SharedDict>(m, "SharedDict")
        .def(nb::init<const std::string &, nb::object, size_t, bool, size_t>(),
             nb::arg("name"),
             nb::arg("data") = nb::none(),
             nb::arg("size") = DEFAULT_SIZE,
             nb::arg("create") = true,
             nb::arg("max_keys") = DEFAULT_MAX_KEYS,
             "Create or open a shared memory dictionary")
        .def("close", &SharedDict::close,
             "Close access to shared memory without removing it")
        .def("unlink", &SharedDict::unlink,
             "Remove the shared memory segment entirely")
        .def("is_closed", &SharedDict::is_closed,
             "Check if this SharedDict connection has been closed")
        .def("__len__", &SharedDict::__len__)
        .def("__contains__", &SharedDict::__contains__)
        .def("__getitem__", &SharedDict::__getitem__)
        .def("__setitem__", &SharedDict::__setitem__)
        .def("__delitem__", &SharedDict::__delitem__)
        .def("get", &SharedDict::get,
             nb::arg("key"),
             nb::arg("default") = nb::none())
        .def("keys", &SharedDict::keys,
             "Return list of all keys")
        .def("values", &SharedDict::values,
             "Return list of all values")
        .def("items", &SharedDict::items,
             "Return list of (key, value) tuples")
        .def("get_stats", &SharedDict::get_stats,
             "Get runtime statistics and diagnostic information")
        .def("recommend_sizing", &SharedDict::recommend_sizing,
             nb::arg("target_entries") = nb::none(),
             "Get sizing recommendations based on current usage");
}