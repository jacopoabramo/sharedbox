# API Reference

## SharedDict

`SharedDict` is a dictionary-like container that stores data in shared memory, allowing multiple processes to access the same data efficiently using Boost.Interprocess.

### Constructor

```python
SharedDict(name: str, data: dict = None, *, size: int = 128 * 1024 * 1024, create: bool = True, max_keys: int = 128)
```

Creates or connects to a shared memory dictionary.

**Parameters:**
- `name` (str): Name of the shared memory segment
- `data` (dict, optional): Initial data to populate the dictionary with
- `size` (int): Size of the shared memory segment in bytes (default: 128MB)
- `create` (bool): Whether to create the segment if it doesn't exist (default: True)
- `max_keys` (int): Maximum number of keys the dictionary can hold (default: 128)

**Example:**
```python
from sharedbox import SharedDict

# Create a new shared dictionary
shared_dict = SharedDict("my_dict", size=64*1024*1024, max_keys=1000)

# Connect to existing shared dictionary
existing_dict = SharedDict("my_dict", create=False)

# Initialize with data
initial_data = {"key1": "value1", "key2": [1, 2, 3]}
shared_dict = SharedDict("initialized_dict", data=initial_data)
```

### Dictionary Operations

#### Basic Access

```python
# Set item
shared_dict[key] = value

# Get item
value = shared_dict[key]  # Raises KeyError if not found

# Get with default
value = shared_dict.get(key, default_value)

# Delete item
del shared_dict[key]  # Raises KeyError if not found

# Check if key exists
if key in shared_dict:
    # key exists
    pass

# Get length
num_items = len(shared_dict)
```

#### Iteration

```python
# Iterate over keys
for key in shared_dict:
    print(key)

# Get all keys
keys = shared_dict.keys()

# Get atomic snapshot of keys (locks all stripes)
keys = shared_dict.keys_atomic()

# Get all items
items = shared_dict.items()

# Get all values
values = shared_dict.values()
```

### Memory Management

#### Connection Management

```python
# Close connection to shared memory (doesn't remove the segment)
shared_dict.close()

# Check if connection is closed
is_closed = shared_dict.is_closed()

# Remove shared memory segment entirely (call after close())
shared_dict.close()
shared_dict.unlink()
```

**Important Notes:**
- `close()` only closes the connection but leaves shared memory intact for other processes
- `unlink()` removes the shared memory segment entirely and should only be called by the creating process
- `unlink()` requires the connection to be closed first

### Data Types Support

SharedDict supports serialization of:
- **Built-in Python types:** int, float, str, bool, list, dict, tuple, etc.
- **NumPy arrays:** Optimized serialization without pickle overhead
- **Any pickle-serializable objects**

**NumPy Array Support:**
```python
import numpy as np

# Store NumPy arrays efficiently
shared_dict["matrix"] = np.array([[1, 2], [3, 4]])
shared_dict["vector"] = np.random.rand(1000)

# Arrays are automatically serialized/deserialized
matrix = shared_dict["matrix"]  # Returns np.ndarray
```

### Statistics and Monitoring

#### Runtime Statistics

```python
stats = shared_dict.get_stats()
```

Returns a dictionary with:
- `total_entries`: Number of key-value pairs
- `sample_size`: Size of sample used for estimations
- `avg_key_utf8_bytes`: Average key size in bytes
- `avg_value_pickle_bytes`: Average serialized value size
- `estimated_data_bytes`: Estimated total data size
- `segment_name`: Name of the shared memory segment

#### Sizing Recommendations

```python
recommendations = shared_dict.recommend_sizing(target_entries=10000)
```

Returns sizing recommendations based on current usage patterns:
- `current_stats`: Current statistics
- `target_entries`: Target number of entries
- `sizing_recommendation`: Recommended segment size
- `lock_recommendation`: Recommended lock configuration

### Thread Safety

SharedDict is thread-safe and uses striped locking for performance:
- Multiple threads can read/write different keys concurrently
- Keys are distributed across multiple lock stripes to minimize contention
- `keys_atomic()` provides a consistent snapshot by locking all stripes

### Error Handling

**Common Exceptions:**
- `KeyError`: Raised when accessing non-existent keys
- `RuntimeError`: Raised for memory management errors (e.g., unlinking open segment)
- `TypeError`: Raised for invalid key types (only strings are supported)
- `ValueError`: Raised for serialization/deserialization errors

### Best Practices

1. **Memory Sizing:**
   ```python
   # Use recommend_sizing() to determine optimal segment size
   recommendations = shared_dict.recommend_sizing(target_entries=50000)
   optimal_size = recommendations['sizing_recommendation']['total_segment_size']
   ```

2. **Process Lifecycle:**
   ```python
   # Creating process
   shared_dict = SharedDict("data", size=optimal_size)
   # ... use shared_dict ...
   shared_dict.close()
   shared_dict.unlink()  # Remove when done

   # Consumer processes
   shared_dict = SharedDict("data", create=False)
   # ... use shared_dict ...
   shared_dict.close()  # Don't unlink in consumer processes
   ```

3. **Error Handling:**
   ```python
   try:
       value = shared_dict[key]
   except KeyError:
       # Handle missing key
       value = default_value
   ```

4. **NumPy Arrays:**
   ```python
   # Ensure arrays are contiguous for optimal performance
   if not array.flags.c_contiguous:
       array = np.ascontiguousarray(array)
   shared_dict["array"] = array
   ```

### Performance Considerations

- **Key Distribution:** Keys are hashed and distributed across lock stripes for concurrency
- **Memory Layout:** Data is stored contiguously in shared memory for cache efficiency
- **Serialization:** NumPy arrays use optimized binary format; other objects use pickle
- **Lock Contention:** Use more lock stripes for higher concurrency workloads

### Example: Multi-Process Usage

```python
# Process 1 (Producer)
from sharedbox import SharedDict
import numpy as np

# Create and populate shared dictionary
shared_dict = SharedDict("sensor_data", size=256*1024*1024, max_keys=10000)
shared_dict["timestamp"] = time.time()
shared_dict["readings"] = np.random.rand(1000)
shared_dict["metadata"] = {"sensor_id": "temp_01", "location": "room_a"}

# Process 2 (Consumer)
from sharedbox import SharedDict

# Connect to existing shared dictionary
shared_dict = SharedDict("sensor_data", create=False)
timestamp = shared_dict["timestamp"]
readings = shared_dict["readings"]  # NumPy array
metadata = shared_dict["metadata"]  # Regular dict

# Clean up (only in the creating process)
shared_dict.close()
shared_dict.unlink()
```
