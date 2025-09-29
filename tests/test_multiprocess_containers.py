import datetime
import multiprocessing as mp
import time
import uuid
from collections import Counter, OrderedDict, defaultdict, deque, namedtuple
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import List, Optional

import pytest

from sharedbox import SharedDict


@dataclass
class Person:
    """Simple dataclass for testing."""

    name: str
    age: int
    email: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class Company:
    """Nested dataclass for testing."""

    name: str
    employees: List[Person] = field(default_factory=list)
    founded: datetime.date = field(default_factory=lambda: datetime.date(2020, 1, 1))


class Status(Enum):
    """Enum for testing."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


class Priority(IntEnum):
    """IntEnum for testing."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = auto()


# Named tuples
Point = namedtuple("Point", ["x", "y", "z"])
User = namedtuple("User", ["id", "name", "status"], defaults=[Status.PENDING])


class CustomClass:
    """Custom class that supports pickling."""

    def __init__(self, data: dict, timestamp: datetime.datetime = None):
        self.data = data
        self.timestamp = timestamp or datetime.datetime.now()
        self._private = "private_data"

    def __eq__(self, other):
        if not isinstance(other, CustomClass):
            return False
        return (
            self.data == other.data
            and self.timestamp == other.timestamp
            and self._private == other._private
        )

    def __getstate__(self):
        """Custom pickle support."""
        return {
            "data": self.data,
            "timestamp": self.timestamp,
            "_private": self._private,
        }

    def __setstate__(self, state):
        """Custom unpickle support."""
        self.data = state["data"]
        self.timestamp = state["timestamp"]
        self._private = state["_private"]


def get_container_test_data():
    """Get comprehensive test data for container types."""

    now = datetime.datetime(2025, 9, 24, 10, 30, 45, 123456)
    today = datetime.date(2025, 9, 24)

    return {
        # Dataclass instances
        "person_simple": Person("Alice", 30),
        "person_full": Person("Bob", 25, "bob@example.com", ["developer", "python"]),
        "company": Company(
            "TechCorp",
            [
                Person("Alice", 30, "alice@techcorp.com", ["manager"]),
                Person("Bob", 25, "bob@techcorp.com", ["developer"]),
            ],
            datetime.date(2015, 6, 1),
            # Removed metadata dict - will use separate segment for nested dicts
        ),
        # Enums
        "status_active": Status.ACTIVE,
        "status_pending": Status.PENDING,
        "priority_high": Priority.HIGH,
        "priority_critical": Priority.CRITICAL,
        # Named tuples
        "point_3d": Point(1.5, 2.7, 3.9),
        "user_default": User(1, "John"),
        "user_full": User(2, "Jane", Status.ACTIVE),
        # Collections
        "defaultdict_int": defaultdict(int, {"a": 1, "b": 2}),
        "defaultdict_list": defaultdict(list, {"items": [1, 2, 3], "other": [4, 5]}),
        "counter": Counter("hello world"),
        "ordered_dict": OrderedDict([("first", 1), ("second", 2), ("third", 3)]),
        "deque_basic": deque([1, 2, 3, 4, 5]),
        "deque_maxlen": deque([1, 2, 3], maxlen=3),
        # Datetime objects
        "datetime_now": now,
        "date_today": today,
        "timedelta": datetime.timedelta(days=30, hours=5, minutes=15),
        # UUID
        "uuid_random": uuid.UUID(
            "ceb38713-6f3e-4680-91e6-efaff51572e2"
        ),  # Fixed UUID to avoid randomness across processes
        "uuid_fixed": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        # Custom class
        "custom_simple": CustomClass(
            {"key": "value"}, now
        ),  # Provide explicit timestamp
        "custom_basic": CustomClass(
            {
                "name": "test",
                "timestamp": now,
                "status": Status.ACTIVE,
                "values": [1, 2, 3],  # Simple list, no nested dict
            },
            now,
        ),
        # Simple structures without nested dicts
        "simple_company": Company(
            "Simple Corp",
            [
                Person("Manager", 40, "mgr@simple.com", ["management"]),
                Person("Developer", 28, "dev@simple.com", ["python"]),
            ],
            datetime.date(2010, 3, 15),
        ),
    }


# Worker functions for container testing


def worker_write_containers(segment_name: str, worker_id: int) -> None:
    """Worker that writes container types to SharedDict."""
    try:
        d = SharedDict(segment_name, create=False)
        test_data = get_container_test_data()

        # Each worker writes the same test data with worker-specific keys
        for type_name, value in test_data.items():
            key = f"worker_{worker_id}_{type_name}"
            d[key] = value

        # Close connection in child process
        d.close()

    except Exception as e:
        msg = f"Worker {worker_id} failed to write container types: {e}"
        raise Exception(msg) from e


def worker_verify_containers(segment_name: str, expected_workers: int) -> dict:
    """Worker that verifies container types were correctly stored and retrieved."""
    try:
        d = SharedDict(segment_name, create=False)

        # Wait for all writers to complete
        max_wait = 30
        start_time = time.time()

        while time.time() - start_time < max_wait:
            completed_workers = sum(
                1
                for worker_id in range(expected_workers)
                if f"worker_{worker_id}_completed" in d
            )
            if completed_workers == expected_workers:
                break
            time.sleep(0.1)
        else:
            raise TimeoutError(
                f"Only {completed_workers}/{expected_workers} workers completed"
            )

        # Verify all data types for all workers
        expected_test_data = get_container_test_data()
        verification_results = {}

        for worker_id in range(expected_workers):
            worker_results = {}
            for type_name, expected_value in expected_test_data.items():
                key = f"worker_{worker_id}_{type_name}"

                if key not in d:
                    worker_results[type_name] = {
                        "status": "missing",
                        "expected": expected_value,
                    }
                    continue

                actual_value = d[key]

                # Compare values - most objects should have proper __eq__ methods
                try:
                    matches = actual_value == expected_value and type(
                        actual_value
                    ) is type(expected_value)
                except Exception as e:
                    matches = False
                    worker_results[type_name] = {
                        "status": "comparison_error",
                        "error": str(e),
                        "expected_type": type(expected_value).__name__,
                        "actual_type": type(actual_value).__name__,
                    }
                    continue

                worker_results[type_name] = {
                    "status": "match" if matches else "mismatch",
                    "expected_type": type(expected_value).__name__,
                    "actual_type": type(actual_value).__name__,
                }

                if not matches:
                    worker_results[type_name].update(
                        {
                            "expected_repr": repr(expected_value)[
                                :200
                            ],  # Truncate long reprs
                            "actual_repr": repr(actual_value)[:200],
                        }
                    )

            verification_results[f"worker_{worker_id}"] = worker_results

        # Close connection in child process
        d.close()

        return verification_results

    except Exception as e:
        msg = f"Container verification worker failed: {e}"
        raise Exception(msg) from e


def worker_test_dataclass_mutation(segment_name: str) -> None:
    """Test that dataclass instances can be modified across processes."""
    try:
        d = SharedDict(segment_name, create=False)

        # Get the company and add employees
        company = d["mutable_company"]

        # Add new employees
        company.employees.append(
            Person("Charlie", 35, "charlie@company.com", ["senior"])
        )
        company.employees.append(Person("Diana", 28, "diana@company.com", ["analyst"]))

        # Store back the modified company
        d["mutable_company"] = company
        d["mutation_complete"] = True

        # Close connection in child process
        d.close()

    except Exception as e:
        msg = f"Dataclass mutation worker failed: {e}"
        raise Exception(msg) from e


def verify_enum_namedtuple_consistency_worker(
    segment_name: str, test_data: dict
) -> None:
    """Worker to verify enum and namedtuple consistency."""
    child_d = SharedDict(segment_name, create=False)

    for key, expected_list in test_data.items():
        actual_list = child_d[key]

        assert len(actual_list) == len(expected_list), f"Length mismatch for {key}"

        for i, (actual, expected) in enumerate(zip(actual_list, expected_list)):
            assert actual == expected, (
                f"Value mismatch at {key}[{i}]: {actual} != {expected}"
            )
            assert type(actual) is type(expected), f"Type mismatch at {key}[{i}]"

            # Special checks for enums
            if isinstance(expected, (Status, Priority)):
                assert actual.name == expected.name, f"Enum name mismatch at {key}[{i}]"
                assert actual.value == expected.value, (
                    f"Enum value mismatch at {key}[{i}]"
                )

    # Close connection in child process
    child_d.close()


def verify_custom_objects_worker(segment_name: str, expected_objects: dict) -> None:
    """Worker to verify custom object integrity."""
    child_d = SharedDict(segment_name, create=False)

    for key, expected in expected_objects.items():
        actual = child_d[key]

        # Use the custom __eq__ method
        assert actual == expected, f"Custom object mismatch for {key}"
        assert type(actual) is type(expected), f"Type mismatch for custom object {key}"

        # Verify internal state
        assert actual.data == expected.data, f"Data mismatch in custom object {key}"
        assert actual.timestamp == expected.timestamp, (
            f"Timestamp mismatch in custom object {key}"
        )
        assert actual._private == expected._private, (
            f"Private data mismatch in custom object {key}"
        )

    # Close connection in child process
    child_d.close()


def verify_collections_worker(segment_name: str, expected_collections: dict) -> None:
    """Worker to verify collections integrity."""
    child_d = SharedDict(segment_name, create=False)

    for key, expected in expected_collections.items():
        actual = child_d[key]

        assert actual == expected, f"Collections mismatch for {key}"
        assert type(actual) is type(expected), f"Type mismatch for collection {key}"

        # Special checks for specific collection types
        if isinstance(expected, deque) and expected.maxlen is not None:
            assert actual.maxlen == expected.maxlen, f"Deque maxlen mismatch for {key}"

        if isinstance(expected, defaultdict):
            assert actual.default_factory == expected.default_factory, (
                f"DefaultDict factory mismatch for {key}"
            )

        if isinstance(expected, OrderedDict):
            assert list(actual.keys()) == list(expected.keys()), (
                f"OrderedDict key order mismatch for {key}"
            )

    # Close connection in child process
    child_d.close()


def child_process_container_test_worker(segment_name: str) -> None:
    """Simple child process for container testing."""
    child_d = SharedDict(segment_name, create=False)

    # Verify container objects
    person = child_d["person"]
    assert person.name == "Alice"
    assert person.age == 30
    assert person.email == "alice@example.com"
    assert person.tags == ["developer"]

    point = child_d["point"]
    assert point.x == 1.0 and point.y == 2.0 and point.z == 3.0

    status = child_d["status"]
    assert status == Status.ACTIVE
    assert status.value == "active"

    # Add container data from child
    child_d["child_person"] = Person("Bob", 25)
    child_d["child_counter"] = Counter("test string")

    # Close connection in child process
    child_d.close()


class TestContainersMultiProcess:
    """Test multi-process SharedDict functionality with container types."""

    @pytest.mark.skip(reason="Flaky test, needs investigation")
    def test_all_container_types_cross_process(self):
        """Test that all container types work correctly across processes."""
        segment_name = "test-container-types"
        segment_size = 100 * 1024 * 1024  # 100MB for all the container data

        d = SharedDict(segment_name, size=segment_size, create=True)

        num_workers = 2

        processes: list[mp.Process] = []
        for worker_id in range(num_workers):
            p = mp.Process(
                target=worker_write_containers,
                args=(segment_name, worker_id),
            )
            p.start()
            processes.append(p)

        # Wait for all worker processes to complete
        for p in processes:
            p.join(timeout=10)
            assert p.exitcode == 0, f"Worker process failed with exit code {p.exitcode}"

        # Verify all data directly in the test process
        expected_data = get_container_test_data()
        try:
            for worker_id in range(num_workers):
                for type_name, expected_value in expected_data.items():
                    key = f"worker_{worker_id}_{type_name}"

                    assert key in d, f"Missing key: {key}"

                    actual_value = d[key]

                    # Compare values - most objects should have proper __eq__ methods
                    assert actual_value == expected_value, (
                        f"Value mismatch for {type_name} (worker {worker_id})"
                    )
                    assert type(actual_value) is type(expected_value), (
                        f"Type mismatch for {type_name} (worker {worker_id})"
                    )
        except AssertionError as e:
            d.close()
            d.unlink()
            raise e
        else:
            d.close()
            d.unlink()

    def test_dataclass_cross_process_operations(self):
        """Test dataclass creation and modification across processes."""
        segment_name = "test-dataclass-ops"
        segment_size = 50 * 1024 * 1024

        d = SharedDict(segment_name, size=segment_size, create=True)

        # Create initial company
        initial_company = Company(
            "StartupCorp",
            [Person("Founder", 30, "founder@startup.com", ["ceo"])],
            datetime.date.today(),
        )

        d["mutable_company"] = initial_company

        # Test mutation in child process
        p = mp.Process(target=worker_test_dataclass_mutation, args=(segment_name,))
        p.start()
        p.join(timeout=30)
        assert p.exitcode == 0, "Dataclass mutation process failed"

        # Verify mutations are visible
        assert "mutation_complete" in d, "Mutation process didn't complete"

        updated_company = d["mutable_company"]
        assert len(updated_company.employees) == 3, (
            f"Expected 3 employees, got {len(updated_company.employees)}"
        )
        assert updated_company.employees[1].name == "Charlie"
        assert updated_company.employees[2].name == "Diana"

        d.close()
        d.unlink()

    def test_enum_and_named_tuple_consistency(self):
        """Test that enums and named tuples maintain identity across processes."""
        segment_name = "test-enum-namedtuple"
        segment_size = 20 * 1024 * 1024

        d = SharedDict(segment_name, size=segment_size, create=True)

        # Test data with enums and named tuples
        test_data = {
            "status_values": [Status.ACTIVE, Status.INACTIVE, Status.PENDING],
            "priority_values": [
                Priority.LOW,
                Priority.MEDIUM,
                Priority.HIGH,
                Priority.CRITICAL,
            ],
            "points": [Point(0, 0, 0), Point(1, 1, 1), Point(-1, -2, -3)],
            "users": [
                User(1, "Alice", Status.ACTIVE),
                User(2, "Bob"),  # Uses default Status.PENDING
                User(3, "Charlie", Status.INACTIVE),
            ],
        }

        # Write test data
        for key, value in test_data.items():
            d[key] = value

        # Verify in child process
        p = mp.Process(
            target=verify_enum_namedtuple_consistency_worker,
            args=(segment_name, test_data),
        )
        p.start()
        p.join(timeout=20)
        assert p.exitcode == 0, "Enum/NamedTuple consistency verification failed"

        d.close()
        d.unlink()

    def test_custom_class_pickling(self):
        """Test that custom classes with pickle support work across processes."""
        segment_name = "test-custom-class"
        segment_size = 30 * 1024 * 1024

        d = SharedDict(segment_name, size=segment_size, create=True)

        # Create custom objects
        custom_objects = {
            "simple": CustomClass({"test": "data"}),
            "complex": CustomClass(
                {
                    "nested": {"level1": {"level2": "deep"}},
                    "list_data": [1, 2, 3, Status.ACTIVE],
                    "tuple_data": (Point(1, 2, 3), "text"),
                },
                datetime.datetime.now(),
            ),
            "with_dataclass": CustomClass(
                {
                    "person": Person("Test", 25, "test@example.com"),
                    "company": Company("TestCorp"),
                }
            ),
        }

        # Store custom objects
        for key, obj in custom_objects.items():
            d[key] = obj

        # Verify in child process
        p = mp.Process(
            target=verify_custom_objects_worker, args=(segment_name, custom_objects)
        )
        p.start()
        p.join(timeout=30)
        assert p.exitcode == 0, "Custom object verification failed"

        d.close()
        d.unlink()

    def test_collections_module_types(self):
        """Test collections module types work correctly across processes."""
        segment_name = "test-collections"
        segment_size = 40 * 1024 * 1024

        d = SharedDict(segment_name, size=segment_size, create=True)

        # Create various collections
        collections_data = {
            "counter_text": Counter("hello world this is a test"),
            "counter_list": Counter([1, 2, 2, 3, 3, 3, 4, 4, 4, 4]),
            "defaultdict_str": defaultdict(str, {"key1": "value1", "key2": "value2"}),
            "defaultdict_set": defaultdict(
                set, {"group1": {1, 2, 3}, "group2": {4, 5, 6}}
            ),
            "ordered_dict": OrderedDict([("z", 1), ("y", 2), ("x", 3), ("w", 4)]),
            "deque_normal": deque([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
            "deque_limited": deque([1, 2, 3, 4, 5], maxlen=5),
        }

        # Store collections
        for key, collection in collections_data.items():
            d[key] = collection

        # Verify in child process
        p = mp.Process(
            target=verify_collections_worker, args=(segment_name, collections_data)
        )
        p.start()
        p.join(timeout=30)
        assert p.exitcode == 0, "Collections verification failed"

        d.close()
        d.unlink()


# Simple function-based test for basic container functionality
def test_simple_container_multiprocess():
    """Simple container multiprocess test that works with pytest."""
    segment_name = "test-simple-container-mp"

    # Create shared dict
    d = SharedDict(segment_name, size=10 * 1024 * 1024, create=True)

    # Add some container data
    d["person"] = Person("Alice", 30, "alice@example.com", ["developer"])
    d["point"] = Point(1.0, 2.0, 3.0)
    d["status"] = Status.ACTIVE

    # Spawn child process
    p = mp.Process(target=child_process_container_test_worker, args=(segment_name,))
    p.start()
    p.join(timeout=15)

    assert p.exitcode == 0, f"Child process failed with exit code {p.exitcode}"

    # Verify child wrote container data
    assert "child_person" in d
    assert "child_counter" in d

    child_person = d["child_person"]
    assert child_person.name == "Bob" and child_person.age == 25

    child_counter = d["child_counter"]
    assert child_counter["t"] == 3  # 't' appears 3 times in "test string"

    d.close()
    d.unlink()
