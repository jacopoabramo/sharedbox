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
from typing import Annotated

import numpy as np
import pytest

import sharedbox
from sharedbox import (
    BoxClosedError,
    Capacity,
    DType,
    MaxShape,
    SharedBox,
    SharedStream,
    StreamClosedError,
)
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


def handle_of(capsule: object, name: bytes = b"sharedbox_box") -> Handle:
    return Handle.from_address(get_pointer(capsule, name))


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
    library.consumer_read_float32.restype = ctypes.c_int
    library.consumer_read_float32.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.POINTER(ctypes.c_float),
    ]
    library.consumer_take_stream.restype = ctypes.c_void_p
    library.consumer_take_stream.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    library.consumer_send_ints.restype = ctypes.c_int
    library.consumer_send_ints.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int64,
        ctypes.c_uint32,
        ctypes.c_double,
    ]
    library.consumer_receive_ints.restype = ctypes.c_int
    library.consumer_receive_ints.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_uint32,
        ctypes.c_double,
    ]
    library.consumer_release_stream.restype = None
    library.consumer_release_stream.argtypes = [ctypes.c_void_p]
    library.consumer_release.restype = None
    library.consumer_release.argtypes = [ctypes.c_void_p]
    return library


def test_get_include_holds_the_headers() -> None:
    """Check that get_include points to a folder with every sharedbox header and the C source."""
    folder = Path(sharedbox.get_include()) / "sharedbox"
    for name in (
        "core.hpp",
        "box.hpp",
        "sharedbox.hpp",
        "sharedbox_c.h",
        "sharedbox_c.cpp",
    ):
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
        assert ctypes.string_at(handle.base, 8) == b"SBX_BOX_"


def test_an_unused_capsule_outlives_close_and_unlink(unique_name: str) -> None:
    """Check that the memory behind a capsule stays readable after the box is closed and unlinked."""
    box = Frame.create(unique_name, 0.5, 3)
    capsule = box.__sharedbox_box__(max_version=LAYOUT_VERSION)
    handle = handle_of(capsule)
    box.close()
    Frame.unlink(unique_name)
    assert ctypes.string_at(handle.base, 8) == b"SBX_BOX_"
    del capsule, handle
    gc.collect()


def test_requests_the_box_cannot_meet_are_refused(unique_name: str) -> None:
    """Check that the capsule request refuses a newer major version and a stream, and a closed box raises BoxClosedError."""
    with Frame.create(unique_name) as box:
        with pytest.raises(BufferError, match="major version 4"):
            box.__sharedbox_box__(max_version=(4, 0))
        with pytest.raises(NotImplementedError, match="stream"):
            box.__sharedbox_box__(stream=True)
    with pytest.raises(BoxClosedError):
        box.__sharedbox_box__()


def test_the_stream_capsule_holds_a_handle_on_the_stream(unique_name: str) -> None:
    """Check that the stream capsule's handle reports the stream's layout version, name and segment start."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        capsule = stream.__sharedbox_stream__()
        handle = handle_of(capsule, b"sharedbox_stream")
        assert (handle.layout_major, handle.layout_minor, handle.handle_version) == (
            *stream._native.layout_version,
            1,
        )
        assert handle.name == unique_name.encode()
        assert ctypes.string_at(handle.base, 8) == b"SBX_STRM"


def test_an_unused_stream_capsule_outlives_close_and_unlink(unique_name: str) -> None:
    """Check that the memory behind a stream capsule stays readable after the stream is closed and unlinked."""
    stream = SharedStream.create(int, unique_name, capacity=2)
    capsule = stream.__sharedbox_stream__()
    handle = handle_of(capsule, b"sharedbox_stream")
    stream.close()
    SharedStream.unlink(unique_name)
    assert ctypes.string_at(handle.base, 8) == b"SBX_STRM"
    del capsule, handle
    gc.collect()


def test_requests_the_stream_cannot_meet_are_refused(unique_name: str) -> None:
    """Refuse another major, an older minor and unknown keywords, accept the stream's own version, and raise StreamClosedError once closed."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        major, minor = stream._native.layout_version
        with pytest.raises(BufferError, match=f"major version {major + 1}"):
            stream.__sharedbox_stream__(max_version=(major + 1, 0))
        with pytest.raises(BufferError, match=f"needs layout {major}.{minor} or newer"):
            stream.__sharedbox_stream__(max_version=(major, minor - 1))
        stream.__sharedbox_stream__(max_version=(major, minor))
        with pytest.raises(NotImplementedError, match="sender"):
            stream.__sharedbox_stream__(sender=True)
    with pytest.raises(StreamClosedError):
        stream.__sharedbox_stream__()


def test_a_stream_of_a_newer_minor_meets_this_version(unique_name: str) -> None:
    """Accept this version's max_version for a stream whose header names a newer minor."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        major, minor = stream._native.layout_version
        capsule = stream.__sharedbox_stream__()
        # kind_minor of the common line, at offset 14.
        ctypes.c_uint16.from_address(
            handle_of(capsule, b"sharedbox_stream").base + 14
        ).value = minor + 1
        with SharedStream.attach(int, unique_name) as again:
            assert again._native.layout_version == (major, minor)
            again.__sharedbox_stream__(max_version=(major, minor))


class Bytes(SharedBox):
    small: Annotated[list[np.uint8], Capacity(4)]


class Image(SharedBox):
    image: Annotated[np.ndarray, MaxShape((4, 4)), DType("uint8")]


@pytest.mark.parametrize(
    ("cls", "values", "needed"),
    [
        (Frame, (), 0),
        (Bytes, ([1],), 1),
        (Image, (np.zeros((2, 2), np.uint8),), 3),
    ],
)
def test_a_minor_older_than_the_box_needs_is_refused(
    unique_name: str, cls: type[SharedBox], values: tuple[object, ...], needed: int
) -> None:
    """Refuse a max_version minor below the oldest whose readers open the box, and accept that one."""
    with cls.create(unique_name, *values) as box:
        if needed > 0:
            with pytest.raises(BufferError, match=f"needs layout 3.{needed} or newer"):
                box.__sharedbox_box__(max_version=(3, needed - 1))
        box.__sharedbox_box__(max_version=(3, needed))


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


class Weighing(SharedBox):
    grams: np.float32 = np.float32(1.5)


USED_STREAM_NAME = b"used_sharedbox_stream"


def take_stream(consumer: ctypes.CDLL, capsule: object) -> int:
    code = ctypes.c_int()
    taken = consumer.consumer_take_stream(
        get_pointer(capsule, b"sharedbox_stream"), ctypes.byref(code)
    )
    assert taken, code.value
    assert set_name(capsule, USED_STREAM_NAME) == 0
    return taken


def test_python_receives_what_a_cpp_library_sends(
    unique_name: str, consumer: ctypes.CDLL
) -> None:
    """Receive in Python the items a C++ library sends through the stream it took from the capsule."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader(mode="lossless")
        taken = take_stream(consumer, stream.__sharedbox_stream__())
        assert consumer.consumer_send_ints(taken, 7, 3, 1.0) == 0
        assert [reader.receive(timeout=1.0) for _ in range(3)] == [7, 8, 9]
        consumer.consumer_release_stream(taken)


def test_a_cpp_library_receives_what_python_sends_after_close_and_unlink(
    unique_name: str, consumer: ctypes.CDLL
) -> None:
    """Receive in C++ the items Python sent, after the Python stream is closed and unlinked."""
    stream = SharedStream.create(int, unique_name, capacity=4)
    taken = take_stream(consumer, stream.__sharedbox_stream__())
    sender = stream.sender()
    sender.send(4)
    sender.send(5)
    stream.close()
    SharedStream.unlink(unique_name)
    received = (ctypes.c_int64 * 2)()
    assert consumer.consumer_receive_ints(taken, received, 2, 1.0) == 0
    assert list(received) == [4, 5]
    consumer.consumer_release_stream(taken)


def test_a_cpp_library_gets_no_second_sender(
    unique_name: str, consumer: ctypes.CDLL
) -> None:
    """Refuse the sender to a C++ library while Python holds it."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        stream.sender()
        taken = take_stream(consumer, stream.__sharedbox_stream__())
        assert consumer.consumer_send_ints(taken, 1, 1, 1.0) == -13
        consumer.consumer_release_stream(taken)


def test_a_box_capsule_is_not_a_stream(unique_name: str, consumer: ctypes.CDLL) -> None:
    """Refuse a box capsule's handle as a stream and leave it to the box capsule to release."""
    with Frame.create(unique_name) as box:
        capsule = box.__sharedbox_box__()
        code = ctypes.c_int()
        taken = consumer.consumer_take_stream(
            get_pointer(capsule, b"sharedbox_box"), ctypes.byref(code)
        )
        assert not taken
        assert code.value == -11
        assert handle_of(capsule).release is not None
        del capsule
        gc.collect()


def test_a_c_library_reads_a_float32(unique_name: str, consumer: ctypes.CDLL) -> None:
    """Read a float32 field from C through sbx_read_float32."""
    value = ctypes.c_float()
    with Weighing.create(unique_name) as box:
        capsule = box.__sharedbox_box__()
        taken = consumer.consumer_take(
            get_pointer(capsule, b"sharedbox_box"), Weighing.__layout__.schema_hash
        )
        assert taken
        assert set_name(capsule, USED_NAME) == 0
        try:
            assert consumer.consumer_read_float32(taken, 0, ctypes.byref(value)) == 0
        finally:
            consumer.consumer_release(taken)
    assert value.value == 1.5
    Weighing.unlink(unique_name)


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
