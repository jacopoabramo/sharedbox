import ctypes
import datetime
import enum
import gc
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import sharedbox
from sharedbox import BoxClosedError, SharedBox
from sharedbox._native import LAYOUT_VERSION

CONSUMER = Path(__file__).parent / "cpp" / "consumer"
# PyCapsule_SetName keeps the pointer, so the name must outlive the capsule.
USED_NAME = b"used_sharedbox_box"


class Frame(SharedBox):
    exposure: float = 0.0
    count: int = 0


class Handle(ctypes.Structure):
    """``sbx_handle`` from sharedbox_c.h."""

    _fields_ = [
        ("layout_major", ctypes.c_uint16),
        ("layout_minor", ctypes.c_uint16),
        ("handle_version", ctypes.c_uint32),
        ("base", ctypes.c_void_p),
        ("size", ctypes.c_uint64),
        ("name", ctypes.c_char_p),
        ("release", ctypes.c_void_p),
        ("private_data", ctypes.c_void_p),
    ]


get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
get_pointer.restype = ctypes.c_void_p
get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
set_name = ctypes.pythonapi.PyCapsule_SetName
set_name.restype = ctypes.c_int
set_name.argtypes = [ctypes.py_object, ctypes.c_char_p]


def handle_of(capsule: object) -> Handle:
    return Handle.from_address(get_pointer(capsule, b"sharedbox_box"))


@pytest.fixture(scope="session")
def consumer(tmp_path_factory: pytest.TempPathFactory) -> ctypes.CDLL:
    cmake = shutil.which("cmake")
    if cmake is None:
        if os.environ.get("SHAREDBOX_REQUIRE_C_CONSUMER"):
            pytest.fail("building the C consumer needs cmake")
        pytest.skip("building the C consumer needs cmake")
    build = tmp_path_factory.mktemp("consumer")
    prefix = Path(sharedbox.get_include()).parent
    # The installed package's major.minor, which its config version file must accept.
    version = ".".join(importlib.metadata.version("sharedbox").split(".")[:2])
    subprocess.run(
        [
            cmake,
            "-S",
            str(CONSUMER),
            "-B",
            str(build),
            f"-DCMAKE_PREFIX_PATH={prefix}",
            f"-DSHAREDBOX_VERSION={version}",
        ],
        check=True,
    )
    subprocess.run([cmake, "--build", str(build), "--config", "Release"], check=True)
    pattern = "consumer.dll" if sys.platform == "win32" else "libconsumer.so"
    library = ctypes.CDLL(str(next(build.rglob(pattern))))
    library.consumer_take.restype = ctypes.c_void_p
    library.consumer_take.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    library.consumer_read_int.restype = ctypes.c_int
    library.consumer_read_int.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.POINTER(ctypes.c_int64),
    ]
    library.consumer_write_int.restype = ctypes.c_int
    library.consumer_write_int.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.c_int64,
    ]
    library.consumer_read_datetime.restype = ctypes.c_int
    library.consumer_read_datetime.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.POINTER(ctypes.c_int16),
    ]
    library.consumer_read_position.restype = ctypes.c_int
    library.consumer_read_position.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.POINTER(ctypes.c_uint16),
    ]
    library.consumer_release.restype = None
    library.consumer_release.argtypes = [ctypes.c_void_p]
    return library


def test_get_include_holds_the_headers() -> None:
    """Check that get_include points to a folder with the three sharedbox sources."""
    folder = Path(sharedbox.get_include()) / "sharedbox"
    for name in ("sharedbox.hpp", "sharedbox_c.h", "sharedbox_c.cpp"):
        assert (folder / name).is_file()


def test_the_capsule_holds_a_handle_on_the_box(unique_name: str) -> None:
    """Check that the capsule's handle reports the layout version, size, name and segment start of the box."""
    with Frame.create(unique_name, 0.5, 3) as box:
        capsule = box.__sharedbox_box__()
        handle = handle_of(capsule)
        assert (handle.layout_major, handle.layout_minor, handle.handle_version) == (
            *LAYOUT_VERSION,
            1,
        )
        assert handle.size == box._segment._size
        assert handle.name == unique_name.encode()
        assert ctypes.string_at(handle.base, 8) == b"SHREDBX1"


def test_an_unused_capsule_outlives_close_and_unlink(unique_name: str) -> None:
    """Check that the memory behind a capsule stays readable after the box is closed and unlinked."""
    box = Frame.create(unique_name, 0.5, 3)
    capsule = box.__sharedbox_box__(max_version=LAYOUT_VERSION)
    handle = handle_of(capsule)
    box.close()
    Frame.unlink(unique_name)
    assert ctypes.string_at(handle.base, 8) == b"SHREDBX1"
    del capsule, handle
    gc.collect()


def test_requests_the_box_cannot_meet_are_refused(unique_name: str) -> None:
    """Check that the capsule request refuses a newer major version and a stream, and a closed box raises BoxClosedError."""
    with Frame.create(unique_name) as box:
        with pytest.raises(BufferError, match="major version 3"):
            box.__sharedbox_box__(max_version=(3, 0))
        with pytest.raises(NotImplementedError, match="stream"):
            box.__sharedbox_box__(stream=True)
    with pytest.raises(BoxClosedError):
        box.__sharedbox_box__()


def test_a_c_library_keeps_the_box_after_close_and_unlink(
    unique_name: str, consumer: ctypes.CDLL
) -> None:
    """Check that a C library can read and write a box it took from the capsule, refuses a wrong schema hash and keeps working after close and unlink."""
    count = Frame.__layout__.by_name["count"].index
    value = ctypes.c_int64()
    box = Frame.create(unique_name, 0.5, 3)
    capsule = box.__sharedbox_box__()
    schema = Frame.__layout__.schema_hash
    mismatched = box.__sharedbox_box__()
    other_schema = consumer.consumer_take(
        get_pointer(mismatched, b"sharedbox_box"), schema ^ 1
    )
    assert not other_schema
    # Only the schema check rejects a handle after sbx_import has taken it from the capsule.
    assert handle_of(mismatched).release is None
    del mismatched
    taken = consumer.consumer_take(get_pointer(capsule, b"sharedbox_box"), schema)
    assert taken
    assert set_name(capsule, USED_NAME) == 0
    assert consumer.consumer_read_int(taken, count, ctypes.byref(value)) == 0
    assert value.value == 3
    other = Frame.attach(unique_name)
    box.close()
    del capsule
    gc.collect()
    assert consumer.consumer_write_int(taken, count, 9) == 0
    assert other.count == 9
    other.close()
    Frame.unlink(unique_name)
    assert consumer.consumer_read_int(taken, count, ctypes.byref(value)) == 0
    assert value.value == 9
    consumer.consumer_release(taken)


class Level(enum.Enum):
    LOW = 1
    HIGH = 2


class Shot(SharedBox):
    taken: datetime.datetime = datetime.datetime(
        2026, 1, 2, 3, 4, tzinfo=datetime.timezone(datetime.timedelta(hours=1))
    )
    level: Level = Level.HIGH


def test_a_c_library_reads_a_datetime_and_an_enum(
    unique_name: str, consumer: ctypes.CDLL
) -> None:
    """Check that a C library reads a datetime as UTC microseconds with its offset, and an enum as its member's position."""
    layout = Shot.__layout__
    with Shot.create(unique_name) as box:
        capsule = box.__sharedbox_box__()
        taken = consumer.consumer_take(
            get_pointer(capsule, b"sharedbox_box"), layout.schema_hash
        )
        assert taken
        assert set_name(capsule, USED_NAME) == 0
        micros, offset, position = ctypes.c_int64(), ctypes.c_int16(), ctypes.c_uint16()
        when = layout.by_name["taken"].index
        assert (
            consumer.consumer_read_datetime(
                taken, when, ctypes.byref(micros), ctypes.byref(offset)
            )
            == 0
        )
        epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
        assert micros.value == (box.taken - epoch) // datetime.timedelta(microseconds=1)
        assert offset.value == 60
        assert (
            consumer.consumer_read_position(
                taken, layout.by_name["level"].index, ctypes.byref(position)
            )
            == 0
        )
        assert position.value == 1
        consumer.consumer_release(taken)
    Shot.unlink(unique_name)
