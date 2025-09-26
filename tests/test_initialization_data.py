"""
Test for SharedDict initialization with user data.
"""

import numpy as np
import pytest

from sharedbox import SharedDict


@pytest.fixture
def cleanup_names():
    """Fixture to track SharedDict names for cleanup."""
    names = []
    yield names

    # Cleanup
    for name in names:
        try:
            temp_dict = SharedDict(name, create=False)
            temp_dict.close()
            temp_dict.unlink()
        except Exception:
            pass  # Already cleaned up or doesn't exist


class TestSharedDictInitialization:
    """Test SharedDict initialization with data parameter."""

    def test_initialization_with_empty_data(self, cleanup_names):
        """Test initialization with empty dictionary."""
        name = "test_empty_init"
        cleanup_names.append(name)

        data = {}
        shared_dict = SharedDict(name, data, create=True)

        assert len(shared_dict) == 0
        assert list(shared_dict.keys()) == []

        shared_dict.close()
        shared_dict.unlink()

    def test_initialization_with_basic_data(self, cleanup_names):
        """Test initialization with basic Python data types."""
        name = "test_basic_init"
        cleanup_names.append(name)

        data = {
            "string_key": "hello world",
            "int_key": 42,
            "float_key": 3.14,
            "bool_key": True,
            "list_key": [1, 2, 3, "four"],
            "dict_key": {"nested": "value", "count": 100},
        }

        shared_dict = SharedDict(name, data, create=True)

        # Verify all data was stored correctly
        assert len(shared_dict) == len(data)

        for key, expected_value in data.items():
            assert key in shared_dict
            assert shared_dict[key] == expected_value

        shared_dict.close()
        shared_dict.unlink()

    def test_initialization_with_numpy_data(self, cleanup_names):
        """Test initialization with numpy arrays."""
        name = "test_numpy_init"
        cleanup_names.append(name)

        data = {
            "small_array": np.array([1, 2, 3]),
            "float_array": np.array([1.1, 2.2, 3.3], dtype=np.float32),
            "matrix": np.random.randn(5, 5),
            "string_val": "mixed with numpy",
            "int_val": 999,
        }

        shared_dict = SharedDict(name, data, create=True)

        # Verify all data was stored correctly
        assert len(shared_dict) == len(data)

        # Check numpy arrays
        assert np.array_equal(shared_dict["small_array"], data["small_array"])
        assert np.array_equal(shared_dict["float_array"], data["float_array"])
        assert np.array_equal(shared_dict["matrix"], data["matrix"])

        # Check other types
        assert shared_dict["string_val"] == data["string_val"]
        assert shared_dict["int_val"] == data["int_val"]

        shared_dict.close()
        shared_dict.unlink()

    def test_initialization_error_handling(self, cleanup_names):
        """Test error handling during initialization."""
        name = "test_error_init"
        cleanup_names.append(name)

        # Test with non-dictionary data
        with pytest.raises(TypeError, match="Argument 'data' has incorrect type"):
            SharedDict(name, "not a dict", create=True)

        # Test with non-string keys
        with pytest.raises(TypeError, match="All keys must be strings"):
            SharedDict(name, {123: "invalid key type"}, create=True)

        with pytest.raises(TypeError, match="All keys must be strings"):
            SharedDict(name, {"valid": "value", 456: "invalid"}, create=True)

    def test_initialization_partial_failure(self, cleanup_names):
        """Test behavior when initialization fails partway through."""
        name = "test_partial_init"
        cleanup_names.append(name)

        # Create data where serialization might fail
        class UnserializableObject:
            def __reduce__(self):
                raise RuntimeError("Cannot serialize this object")

        data = {
            "good_key1": "valid value",
            "good_key2": 42,
            "bad_key": UnserializableObject(),  # This should cause failure
            "good_key3": "another valid value",
        }

        with pytest.raises(ValueError, match="Failed to initialize SharedDict"):
            SharedDict(name, data, create=True)

    def test_initialization_without_data_parameter(self, cleanup_names):
        """Test that initialization works normally when data parameter is not provided."""
        name = "test_no_data_init"
        cleanup_names.append(name)

        # Should work exactly as before
        shared_dict = SharedDict(name, create=True)

        assert len(shared_dict) == 0

        # Should be able to add data normally
        shared_dict["added_key"] = "added_value"
        assert shared_dict["added_key"] == "added_value"
        assert len(shared_dict) == 1

        shared_dict.close()
        shared_dict.unlink()

    def test_initialization_with_large_data(self, cleanup_names):
        """Test initialization with a larger dataset."""
        name = "test_large_init"
        cleanup_names.append(name)

        # Create larger dataset
        data = {}
        for i in range(100):
            data[f"key_{i}"] = {
                "id": i,
                "name": f"item_{i}",
                "values": list(range(i, i + 10)),
                "array": np.random.randn(20),
            }

        shared_dict = SharedDict(name, data, create=True, size=50 * 1024 * 1024)

        # Verify all data was stored
        assert len(shared_dict) == 100

        # Spot check a few items
        assert shared_dict["key_0"]["id"] == 0
        assert shared_dict["key_50"]["name"] == "item_50"
        assert len(shared_dict["key_99"]["values"]) == 10
        assert shared_dict["key_25"]["array"].shape == (20,)

        shared_dict.close()
        shared_dict.unlink()

    def test_initialization_mixed_with_existing_operations(self, cleanup_names):
        """Test that initialized SharedDict works normally with all operations."""
        name = "test_mixed_ops"
        cleanup_names.append(name)

        initial_data = {
            "initial_key": "initial_value",
            "count": 0,
        }

        shared_dict = SharedDict(name, initial_data, create=True)

        # Test all standard operations still work
        assert "initial_key" in shared_dict
        assert shared_dict.get("initial_key") == "initial_value"
        assert shared_dict.get("missing_key", "default") == "default"

        # Test modification
        shared_dict["count"] = 10
        assert shared_dict["count"] == 10

        # Test addition
        shared_dict["new_key"] = "new_value"
        assert shared_dict["new_key"] == "new_value"

        # Test deletion
        del shared_dict["initial_key"]
        assert "initial_key" not in shared_dict
        assert len(shared_dict) == 2

        # Test iteration
        keys = list(shared_dict.keys())
        assert "count" in keys
        assert "new_key" in keys

        # Test items and values
        items = shared_dict.items()
        values = shared_dict.values()
        assert len(items) == 2
        assert len(values) == 2

        shared_dict.close()
        shared_dict.unlink()


def test_integration_example():
    """Integration example showing typical usage."""

    # Example: Initialize a shared configuration
    config_data = {
        "app_name": "MyApplication",
        "version": "1.0.0",
        "max_users": 1000,
        "features": ["auth", "logging", "caching"],
        "thresholds": {
            "memory": 0.8,
            "cpu": 0.75,
        },
        "model_weights": np.random.randn(100),
    }

    # Create SharedDict with initial configuration
    config = SharedDict("app_config", config_data, create=True)

    try:
        # Verify initialization worked
        assert config["app_name"] == "MyApplication"
        assert config["max_users"] == 1000
        assert len(config["features"]) == 3
        assert config["model_weights"].shape == (100,)

        # Configuration can still be modified
        config["runtime_stats"] = {"requests": 0, "errors": 0}
        config["max_users"] = 2000  # Update existing value

        assert config["runtime_stats"]["requests"] == 0
        assert config["max_users"] == 2000

    finally:
        config.close()
        config.unlink()
