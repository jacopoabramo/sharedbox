# distutils: language = c++
from libcpp.string cimport string
from libcpp.vector cimport vector
from libcpp cimport bool as cbool
import pickle
import struct
from cpython.bytes cimport PyBytes_FromStringAndSize
from cpython.unicode cimport PyUnicode_AsUTF8AndSize, PyUnicode_DecodeUTF8
from .utils import SegmentSizer, LockTuner
import numpy as np

cdef inline int calculate_target_entries(int current_entries, int min_entries) nogil:
    """Pure C function to calculate target entries with 10x growth."""
    cdef int growth_target = current_entries * 10
    return growth_target if growth_target > min_entries else min_entries

cdef inline str _decode_to_str(const string& key_string):
    """Encode a C++ string object to a Python string."""
    return PyUnicode_DecodeUTF8(key_string.c_str(), key_string.size(), NULL)

cdef inline string _encode_to_string(str key):
    """Encode a Python string to a C++ string using UTF-8 encoding."""
    cdef const char* key_ptr
    cdef Py_ssize_t key_len
    key_ptr = PyUnicode_AsUTF8AndSize(key, &key_len)
    return string(key_ptr, key_len)


cdef extern from "shared_dict.hpp" namespace "shared_memory":
    cdef cppclass SharedMemoryDict:
        SharedMemoryDict(const string& name, size_t size, cbool create, size_t max_keys) except +
        void set(const string& k, const string& v) except +
        cbool get(const string& k, string& out) const
        cbool erase(const string& k)
        cbool contains(const string& k) const
        size_t size() const
        vector[string] keys() const
        void close() except +
        void unlink() except +
        cbool is_closed() const

cdef class SharedDict:
    cdef SharedMemoryDict* c_map
    cdef str name

    def __cinit__(self, str name, dict data = None, /, *, int size = 128 * 1024 * 1024, cbool create = True, int max_keys = 128) -> None:
        self.name = name
        cdef string nm = _encode_to_string(name)
        self.c_map = new SharedMemoryDict(nm, <size_t>size, <cbool>create, <size_t>max_keys)
        
        # Initialize with provided data if specified
        if data is not None:
            self._initialize_data(data)

    def __dealloc__(self) -> None:
        """Destructor that releases the connection but does NOT remove shared memory.
        
        This follows the multiprocessing.SharedMemory API pattern:
        - Destructor only closes the connection (like close())
        - Shared memory removal must be done explicitly via unlink()
        - This prevents race conditions when multiple processes use the same segment
        """
        if self.c_map is not NULL:
            # The C++ destructor will call close() if not already closed
            del self.c_map
            self.c_map = NULL

    cpdef void close(self):
        """Close access to shared memory without removing it.
        
        After calling close(), this SharedDict instance becomes unusable
        but other processes can still access the shared memory.
        Similar to multiprocessing.SharedMemory.close().
        """
        if self.c_map is not NULL:
            self.c_map.close()

    cpdef void unlink(self):
        """Remove the shared memory segment entirely.
        
        This removes the shared memory from the system, making it
        inaccessible to all processes. Similar to multiprocessing.SharedMemory.unlink().
        Only call this from the process that created the segment.
        """
        if self.c_map is not NULL:
            if not self.c_map.is_closed():
                raise RuntimeError("Cannot unlink a SharedDict that is still open. Call close() first.")
            self.c_map.unlink()

    cpdef cbool is_closed(self):
        """Check if this SharedDict connection has been closed.
        
        Returns True if close() has been called or the object has been destructed.
        A closed SharedDict cannot perform any operations.
        """
        if self.c_map is NULL:
            return True
        return self.c_map.is_closed()

    cdef bytes _dumps_value(self, object obj):
        """Value serialization with numpy array support."""
        if isinstance(obj, np.ndarray):
            return self._serialize_numpy_array(obj)
        else:
            # Fallback to pickle for other types
            header = b'\x00'  # Non-numpy marker
            data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
            return header + data

    cdef object _loads_value(self, bytes b):
        """Value deserialization with numpy array support."""
        if len(b) == 0:
            raise ValueError("Empty data cannot be deserialized")
        
        cdef unsigned char marker = b[0]
        if marker == 1:  # Numpy array marker
            return self._deserialize_numpy_array(b[1:])
        else:  # Pickle marker (0) or legacy data
            if marker == 0:
                return pickle.loads(b[1:])  # Skip marker
            else:
                return pickle.loads(b)  # Legacy data without marker
    
    cdef bytes _serialize_numpy_array(self, object arr):
        """Efficiently serialize numpy array without pickle."""
        cdef bytes dtype_str = str(arr.dtype).encode('utf-8')
        
        # Get raw array data - ensure it's contiguous
        cdef bytes array_data
        if arr.flags.c_contiguous:
            array_data = arr.tobytes()
        else:
            # Make contiguous copy
            array_data = np.ascontiguousarray(arr).tobytes()
        
        # Pack header: marker(1) + dtype_len(4) + ndim(4) + data_len(4)
        cdef bytes header = struct.pack('<BIII', 
                                       1,  # Numpy marker
                                       len(dtype_str),
                                       arr.ndim, 
                                       len(array_data))
        
        # Pack shape data
        cdef bytes shape_data = b''
        for dim in arr.shape:
            shape_data += struct.pack('<Q', dim)
        
        # Combine all parts
        return header + dtype_str + shape_data + array_data
    
    cdef object _deserialize_numpy_array(self, bytes data):
        """Efficiently deserialize numpy array."""
        cdef int offset = 0
        
        # Unpack header: dtype_len(4) + ndim(4) + data_len(4)  
        dtype_len, ndim, data_len = struct.unpack('<III', data[offset:offset+12])
        offset += 12
        
        # Extract dtype string
        dtype_str = data[offset:offset+dtype_len].decode('utf-8')
        offset += dtype_len
        
        # Extract shape
        cdef list shape = []
        for i in range(ndim):
            dim = struct.unpack('<Q', data[offset:offset+8])[0]
            shape.append(dim)
            offset += 8
        
        # Extract array data
        array_data = data[offset:offset+data_len]
        
        # Reconstruct numpy array
        arr = np.frombuffer(array_data, dtype=dtype_str).reshape(tuple(shape))
        
        # Make a copy to ensure proper memory ownership
        return np.array(arr)

    cdef void _initialize_data(self, dict data):
        """Initialize SharedDict with provided data.
        
        Args:
            data: Dictionary with string keys and values to populate SharedDict.
                  Values must be serializable (built-in types, numpy arrays, etc.)
                  
        Raises:
            TypeError: If data is not a dictionary or contains invalid keys/values
            ValueError: If serialization fails for any value
        """
        if not isinstance(data, dict):
            raise TypeError("Initialization data must be a dictionary")
        
        cdef int initialized_count = 0
        cdef object key_obj
        cdef str key = ""
        cdef object value
        
        try:
            for key_obj, value in data.items():
                if not isinstance(key_obj, str):
                    raise TypeError(f"All keys must be strings, got {type(key_obj)} for key: {key_obj}")
                
                key = <str>key_obj  # Now we know it's safe to cast
                # Use the existing __setitem__ method for consistency
                self[key] = value
                initialized_count += 1
                
        except TypeError as e:
            # Re-raise TypeError with original message for key type errors
            if "All keys must be strings" in str(e):
                raise e
            else:
                raise ValueError(
                    f"Failed to initialize SharedDict during key iteration: {e}"
                ) from e
        except Exception as e:
            # If initialization fails partway through, provide helpful context
            if key:
                raise ValueError(
                    f"Failed to initialize SharedDict after {initialized_count} items. "
                    f"Error on key '{key}': {e}"
                ) from e
            else:
                raise ValueError(
                    f"Failed to initialize SharedDict during iteration: {e}"
                ) from e

    def __len__(self) -> int:
        return <int> self.c_map.size()

    def __contains__(self, str key) -> bool:
        cdef string ks = _encode_to_string(key)
        return bool(self.c_map.contains(ks))

    def __getitem__(self, str key) -> object:
        cdef string ks = _encode_to_string(key)
        cdef string out
        if not self.c_map.get(ks, out):
            raise KeyError(key)
        # Convert std::string (raw bytes) to Python bytes then unpickle
        cdef bytes b = <bytes>PyBytes_FromStringAndSize(out.c_str(), out.size())
        return self._loads_value(b)

    def get(self, str key, object default = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, str key, object value) -> None:
        cdef string ks = _encode_to_string(key)
        cdef bytes vb = self._dumps_value(value)
        cdef string vs = vb
        self.c_map.set(ks, vs)

    def __delitem__(self, str key) -> None:
        cdef string ks = _encode_to_string(key)
        if not self.c_map.erase(ks):
            raise KeyError(key)

    def __iter__(self):
        cdef vector[string] ks = self.c_map.keys()  # Use atomic full-lock version instead of striped
        cdef string s
        for i in range(ks.size()):
            s = ks[i]
            yield _decode_to_str(s)

    def keys(self) -> list[str]:
        return list(iter(self))
    
    def keys_atomic(self) -> list[str]:
        """Get keys with full atomic snapshot (locks all stripes at once)."""
        cdef vector[string] ks = self.c_map.keys()  # Full lock version
        cdef string s
        result: list[str] = []
        for i in range(ks.size()):
            s = ks[i]
            result.append(_decode_to_str(s))
        return result

    def items(self) -> list[tuple[str, object]]:
        return [(k, self[k]) for k in self]

    def values(self) -> list[object]:
        return [self[k] for k in self]
    
    def get_stats(self) -> dict[str, object]:
        """Get runtime statistics and diagnostic information."""
        # Sample some keys to estimate sizes
        cdef vector[string] sample_keys = self.c_map.keys()
        cdef string key_bytes, value_bytes
        sample_size = min(<int>sample_keys.size(), 100)
        
        total_key_bytes = 0
        total_value_bytes = 0
        
        if sample_size > 0:
            for i in range(sample_size):
                key_bytes = sample_keys[i] 
                total_key_bytes += key_bytes.size()
                if self.c_map.get(key_bytes, value_bytes):
                    total_value_bytes += value_bytes.size()
        
        avg_key_bytes = total_key_bytes / sample_size if sample_size > 0 else 0
        avg_value_bytes = total_value_bytes / sample_size if sample_size > 0 else 0
        
        return {
            'total_entries': <int>self.c_map.size(),
            'sample_size': sample_size,
            'avg_key_utf8_bytes': avg_key_bytes,
            'avg_value_pickle_bytes': avg_value_bytes,
            'estimated_data_bytes': <int>self.c_map.size() * (avg_key_bytes + avg_value_bytes),
            'segment_name': self.name,
        }
    
    def recommend_sizing(self, target_entries: int | None = None) -> dict[str, object]:
        """Get sizing recommendations based on current usage."""
        stats = self.get_stats()
        current_entries = stats['total_entries']
        
        if target_entries is None:
            target_entries = calculate_target_entries(current_entries, 10000)
        
        if current_entries == 0:
            return {
                'current_stats': stats,
                'target_entries': target_entries,
                'sizing_recommendation': None,
                'lock_recommendation': None,
                'message': 'No data in SharedMemoryDict yet - cannot provide recommendations'
            }
        
        # Size analysis
        sizing = SegmentSizer.calculate_segment_size(
            target_entries,
            int(stats['avg_key_utf8_bytes']),
            int(stats['avg_value_pickle_bytes'])
        )
        
        # Lock analysis  
        lock_rec = LockTuner.recommend_lock_count(target_entries)
        
        # Return data instead of printing
        
        return {
            'current_stats': stats,
            'target_entries': target_entries,
            'sizing_recommendation': sizing,
            'lock_recommendation': lock_rec
        }
