import contextlib
import mmap
import multiprocessing as mp
import os
import shutil
import struct
import sys
import threading
import time
from collections.abc import Callable, Generator
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Event
from typing import cast

import pytest
from os_names import os_name

from sharedbox import SharedBox
from sharedbox._layout import NativeField
from sharedbox._native import (
    LAYOUT_VERSION,
    BoxClosedError,
    KindMismatchError,
    LockTimeoutError,
    SchemaMismatchError,
    Segment,
    SegmentExistsError,
    SegmentNotFoundError,
    check,
)

BOOL, INT, FLOAT, STR, BYTES, REF = range(6)
FIELDS = [NativeField(0, 8, INT), NativeField(8, 16, BYTES)]
NAMES = ["a", "b"]
RECORD_SIZE = 32
SCHEMA = 0x5EED


def create(name: str, timeout: float = 1.0) -> Segment:
    return Segment.create(name, FIELDS, NAMES, RECORD_SIZE, SCHEMA, timeout, [])


def attach(name: str, timeout: float = 1.0) -> Segment:
    return Segment.attach(name, NAMES, SCHEMA, timeout)


MAGIC = b"SBX_BOX_"
STREAM_MAGIC = b"SBX_STRM"


@contextlib.contextmanager
def raw_bytes(name: str) -> Generator[memoryview, None, None]:
    if sys.platform == "win32":
        shm = SharedMemory(os_name(name))
        try:
            assert shm.buf is not None
            yield shm.buf
        finally:
            shm.close()
    else:
        with (
            open(f"/dev/shm/{os_name(name)}", "r+b") as file,
            mmap.mmap(file.fileno(), 0) as mapping,
            memoryview(mapping) as view,
        ):
            yield view


def patch_header(name: str, offset: int, fmt: str, value: int) -> None:
    with raw_bytes(name) as view:
        struct.pack_into(fmt, view, offset, value)


@contextlib.contextmanager
def foreign_mapping(name: str) -> Generator[None, None, None]:
    """Shared memory under a box's object name, made by software other than sharedbox."""
    if sys.platform == "win32":
        with mmap.mmap(-1, 4096, tagname=os_name(name)):
            yield
    else:
        fd = os.open(
            f"/dev/shm/{os_name(name)}", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
        )
        try:
            os.ftruncate(fd, 4096)
            yield
        finally:
            os.close(fd)


def own_pidns() -> int:
    """This process's pid namespace as sharedbox.hpp records it: 0 on Windows."""
    return 0 if sys.platform == "win32" else os.stat("/proc/self/ns/pid").st_ino


def create_and_exit(name: str, created: Event, attached: Event) -> None:
    segment = create(name)
    created.set()
    attached.wait(20)
    segment.close()


def hold_lock_until_killed(name: str, ready: Event) -> None:
    segment = attach(name)
    segment._hold_write_lock()
    ready.set()
    time.sleep(60)


def shm_free_bytes() -> int:
    """Free bytes in /dev/shm; more than any box where there is none, as on Windows."""
    return shutil.disk_usage("/dev/shm").free if os.path.isdir("/dev/shm") else 1 << 62


def write_pair(name: str, count: int) -> None:
    segment = attach(name)
    for i in range(count):
        segment._write([(0, struct.pack("<q", i)), (1, str(i).encode())])
    segment.close()


def test_attached_segment_sees_writes(unique_name: str) -> None:
    """Check that a segment attached by name reads back what the creating segment wrote."""
    owner = create(unique_name)
    other = attach(unique_name)
    owner._write([(0, struct.pack("<q", 42)), (1, b"hello")])
    assert other._read(0) == struct.pack("<q", 42)
    assert other._read(1) == b"hello"
    assert other._read_all() == [struct.pack("<q", 42), b"hello"]
    other.close()
    owner.close()


def test_new_segment_reads_zero(unique_name: str) -> None:
    """Check that a new segment reads zero for a fixed field and empty bytes for a variable one."""
    segment = create(unique_name)
    assert segment._read_all() == [bytes(8), b""]
    segment.close()


def test_create_refuses_existing_name(unique_name: str) -> None:
    """Check that creating a segment under a taken name raises SegmentExistsError."""
    segment = create(unique_name)
    with pytest.raises(SegmentExistsError):
        create(unique_name)
    segment.close()


def test_attach_to_missing_name(unique_name: str) -> None:
    """Check that attaching to an unknown name raises SegmentNotFoundError."""
    with pytest.raises(SegmentNotFoundError):
        attach(unique_name)


def test_attach_with_other_schema(unique_name: str) -> None:
    """Check that attaching with a different schema hash raises SchemaMismatchError."""
    segment = create(unique_name)
    with pytest.raises(SchemaMismatchError):
        Segment.attach(unique_name, NAMES, SCHEMA + 1, 1.0)
    segment.close()


def test_layout_outside_record_is_rejected(unique_name: str) -> None:
    """Check that a field placed outside the record raises ValueError at create."""
    with pytest.raises(ValueError):
        Segment.create(
            unique_name, [NativeField(32, 8, INT)], ["a"], RECORD_SIZE, SCHEMA, 1.0, []
        )


def test_failed_create_leaves_the_name_free(unique_name: str) -> None:
    """Check that a create that raises ValueError leaves the name free for a later create."""
    with pytest.raises(ValueError):
        Segment.create(
            unique_name, [NativeField(0, 8, INT)], ["a"], 2**40, SCHEMA, 1.0, []
        )
    create(unique_name).close()


def test_oversized_write_changes_nothing(unique_name: str) -> None:
    """Check that a write with one value over its capacity raises ValueError and changes no field or version."""
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment._write([(0, struct.pack("<q", 1)), (1, b"x" * 17)])
    assert segment._read(0) == bytes(8)
    assert segment.version(0) == 0
    segment.close()


def test_fixed_field_needs_exact_size(unique_name: str) -> None:
    """Check that writing a value of the wrong size to a fixed-size field raises ValueError."""
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment._write([(0, b"abc")])
    segment.close()


def test_unknown_field_index(unique_name: str) -> None:
    """Check that reading a field index past the last field raises IndexError."""
    segment = create(unique_name)
    with pytest.raises(IndexError):
        segment._read(2)
    segment.close()


def test_versions_and_generation_count_writes(unique_name: str) -> None:
    """Check that field versions and the generation count each write once."""
    segment = create(unique_name)
    segment._write([(1, b"a")])
    segment._write([(0, bytes(8)), (1, b"b")])
    assert (segment.version(0), segment.version(1), segment.generation()) == (1, 2, 2)
    segment.close()


def test_unlink_removes_the_name_like_shared_memory(unique_name: str) -> None:
    """Check that unlink removes the name on Linux and not on Windows while an open segment keeps working."""
    owner = create(unique_name)
    Segment.unlink(unique_name)
    if sys.platform == "win32":
        attach(unique_name).close()
    else:
        with pytest.raises(SegmentNotFoundError):
            attach(unique_name)
        with pytest.raises(SegmentNotFoundError):
            Segment.unlink(unique_name)
    owner._write([(1, b"still mapped")])
    assert owner._read(1) == b"still mapped"
    owner.close()


def test_close_keeps_the_segment_for_others(unique_name: str) -> None:
    """Check that closing the creating segment leaves the data readable through another handle."""
    owner = create(unique_name)
    other = attach(unique_name)
    owner._write([(1, b"kept")])
    owner.close()
    assert other._read(1) == b"kept"
    other.close()


def test_closed_segment_refuses_use(unique_name: str) -> None:
    """Check that close is idempotent and a read on a closed segment raises BoxClosedError."""
    segment = create(unique_name)
    segment.close()
    segment.close()
    assert segment.closed
    with pytest.raises(BoxClosedError):
        segment._read(0)


def test_held_lock_times_out_and_force_unlock_recovers(unique_name: str) -> None:
    """Check that a held write lock makes reads and writes raise LockTimeoutError until force_unlock."""
    owner = create(unique_name)
    owner._hold_write_lock()
    other = attach(unique_name, timeout=0.2)
    with pytest.raises(LockTimeoutError, match=str(os.getpid())):
        other._write([(1, b"x")])
    with pytest.raises(LockTimeoutError):
        other._read(1)
    other.force_unlock()
    other._write([(1, b"x")])
    assert other._read(1) == b"x"
    other.close()
    owner.close()


def test_close_while_other_threads_read_and_write(unique_name: str) -> None:
    """Check that closing a segment while other threads read and write ends them with BoxClosedError only."""
    segment = create(unique_name)
    errors: list[BaseException] = []

    def until_closed(call: Callable[[], object]) -> None:
        try:
            while True:
                call()
        except BoxClosedError:
            pass
        except BaseException as error:
            errors.append(error)

    def write() -> None:
        segment._write([(0, struct.pack("<q", 1)), (1, b"x")])

    calls = [segment._read_all] * 4 + [write] * 4
    threads = [threading.Thread(target=until_closed, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    segment.close()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert not any(thread.is_alive() for thread in threads)


def test_reads_never_see_half_a_write(unique_name: str) -> None:
    """Check that a reader never sees two fields from different writes of another process."""
    segment = create(unique_name)
    writer = mp.get_context("spawn").Process(
        target=write_pair, args=(unique_name, 50_000)
    )
    writer.start()
    while writer.is_alive():
        number, text = segment._read_all()
        if text:
            assert str(struct.unpack("<q", number)[0]).encode() == text
    writer.join()
    assert writer.exitcode == 0
    segment.close()


BAD_TIMEOUTS = [float("inf"), float("nan"), 0.0, -1.0, 86401.0]


@pytest.mark.parametrize("timeout", BAD_TIMEOUTS)
def test_bad_lock_timeout_is_refused(unique_name: str, timeout: float) -> None:
    """Check that create and attach raise ValueError for each invalid lock timeout."""
    with pytest.raises(ValueError):
        create(unique_name, timeout)
    segment = create(unique_name)
    with pytest.raises(ValueError):
        attach(unique_name, timeout)
    segment.close()


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), -1.0, 86401.0])
def test_bad_wait_timeout_is_refused(unique_name: str, timeout: float) -> None:
    """Check that wait raises ValueError for a non-finite, negative or over-large timeout."""
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment.wait(segment.generation(), timeout)
    segment.close()


def test_small_box_takes_one_page(unique_name: str) -> None:
    """Check that a small box is mapped as a single 4096-byte page."""
    segment = Segment.create(
        unique_name,
        [*FIELDS, NativeField(32, 8, INT)],
        [*NAMES, "c"],
        40,
        SCHEMA,
        1.0,
        [],
    )
    assert segment._size == 4096
    segment.close()


def test_raw_bytes_follow_the_layout(unique_name: str) -> None:
    """Check that the mapped bytes of a segment match the layout field by field."""
    fields = [
        NativeField(0, 8, INT),
        NativeField(8, 8, FLOAT),
        NativeField(16, 1, BOOL),
    ]
    segment = Segment.create(
        unique_name,
        fields,
        ["a", "b", "c"],
        24,
        0x1122334455667788,
        1.0,
        [(0, 7), (2, True)],
    )
    segment.set([(1, 0.5)])
    with raw_bytes(unique_name) as view:
        raw = bytes(view)
    segment.close()
    assert len(raw) == 4096
    assert raw[0:8] == MAGIC
    assert struct.unpack_from("<HH", raw, 8) == (1, 0)
    assert struct.unpack_from("<HH", raw, 12) == LAYOUT_VERSION
    assert struct.unpack_from("<Q", raw, 16) == (0x1122334455667788,)
    create_id, creator_start, creator_pidns, creator_pid = struct.unpack_from(
        "<QQQI", raw, 24
    )
    assert create_id != 0
    assert creator_start != 0
    assert creator_pidns == own_pidns()
    assert creator_pid == os.getpid()
    assert struct.unpack_from("<HHQ", raw, 52) == (64, 0, 4096)
    # seq, writer_pid, wake_word, waiters and sleepers after one write, with no one waiting.
    assert struct.unpack_from("<QIIII", raw, 64) == (2, 0, 1, 0, 0)
    # field_count and its reserved word, then record_size, record, tail and types_size.
    assert struct.unpack_from("<HHIIII", raw, 88) == (3, 0, 24, 1728, 128, 0)
    assert raw[108:128] == bytes(20)
    assert struct.unpack_from("<6I", raw, 128) == (
        0,
        8 | INT << 24,
        8,
        8 | FLOAT << 24,
        16,
        1 | BOOL << 24,
    )
    assert struct.unpack_from("<3Q", raw, 152) == (0, 1, 0)
    assert raw[176:1728] == bytes(1552)
    assert struct.unpack_from("<qd?", raw, 1728) == (7, 0.5, True)
    assert raw[1745:] == bytes(4096 - 1745)


@pytest.mark.parametrize(
    ("offset", "fmt", "value"),
    [
        pytest.param(88, "<H", 0, id="no-fields"),
        pytest.param(88, "<H", 256, id="table-over-the-record"),
        pytest.param(52, "<H", 0, id="no-waiter-slots"),
        pytest.param(52, "<H", 4097, id="too-many-waiter-slots"),
        pytest.param(92, "<I", 4000, id="record-past-the-mapping"),
        pytest.param(96, "<I", 0xFFFF_FFC0, id="record-outside"),
        pytest.param(96, "<I", 128, id="record-over-the-table"),
        pytest.param(96, "<I", 1736, id="record-unaligned"),
        pytest.param(100, "<I", 136, id="tail-moved"),
        pytest.param(56, "<I", 8192, id="size-differs"),
        pytest.param(128, "<I", 32, id="field-past-the-record"),
    ],
)
def test_corrupt_header_is_refused(
    unique_name: str, offset: int, fmt: str, value: int
) -> None:
    """Check that attach raises SchemaMismatchError for each corrupted header value."""
    segment = create(unique_name)
    patch_header(unique_name, offset, fmt, value)
    with pytest.raises(SchemaMismatchError, match="corrupt header"):
        attach(unique_name)
    segment.close()


def test_another_major_version_is_refused(unique_name: str) -> None:
    """Check that attach raises SchemaMismatchError for a different major layout version."""
    segment = create(unique_name)
    patch_header(unique_name, 12, "<H", 4)
    with pytest.raises(
        SchemaMismatchError, match=r"uses core version 1\.0 and box layout 4\.0"
    ):
        attach(unique_name)
    segment.close()


@pytest.mark.parametrize("magic", [b"SHREDBX1", b"NOTSBX00"])
def test_a_magic_this_version_does_not_know_is_refused(
    unique_name: str, magic: bytes
) -> None:
    """Check that attach raises SchemaMismatchError for a box of 0.5 and for an unknown magic."""
    segment = create(unique_name)
    with raw_bytes(unique_name) as view:
        view[0:8] = magic
    with pytest.raises(SchemaMismatchError, match="another version of sharedbox"):
        attach(unique_name)
    with raw_bytes(unique_name) as view:
        view[0:8] = MAGIC
    segment.close()


def test_a_higher_minor_version_opens(unique_name: str) -> None:
    """Check that attach accepts a higher minor layout version."""
    owner = create(unique_name)
    patch_header(unique_name, 14, "<H", 7)
    other = attach(unique_name)
    owner._write([(1, b"minor")])
    assert other._read(1) == b"minor"
    other.close()
    owner.close()


def test_a_name_at_the_length_limit(unique_name: str) -> None:
    """Check that a name of the maximum length can be created and attached."""
    name = (unique_name + "x" * 240)[:240]
    owner = create(name)
    try:
        other = attach(name)
        other._write([(1, b"long")])
        assert owner._read(1) == b"long"
        other.close()
    finally:
        owner.close()
        Segment.unlink(name)


def test_a_foreign_mapping_under_the_name_is_not_a_box(unique_name: str) -> None:
    """Check that a mapping that is not a box makes create raise SegmentExistsError and attach raise SegmentNotFoundError."""
    with foreign_mapping(unique_name):
        with pytest.raises(SegmentExistsError):
            create(unique_name)
        with pytest.raises(SegmentNotFoundError):
            attach(unique_name, timeout=0.2)


def test_a_writer_killed_holding_the_lock(unique_name: str) -> None:
    """Check that a lock left by a killed writer times out naming its pid and force_unlock recovers the box."""
    segment = create(unique_name, timeout=0.3)
    context = mp.get_context("spawn")
    ready = context.Event()
    child = context.Process(target=hold_lock_until_killed, args=(unique_name, ready))
    child.start()
    assert ready.wait(20)
    child.kill()
    child.join(20)
    with pytest.raises(LockTimeoutError, match=rf"locked by pid {child.pid}\b"):
        segment._write([(1, b"x")])
    with pytest.raises(LockTimeoutError):
        segment._read(1)
    segment.force_unlock()
    with raw_bytes(unique_name) as view:
        assert struct.unpack_from("<I", view, 72) == (child.pid,)
    segment._write([(1, b"after")])
    assert segment._read(1) == b"after"
    segment.close()


def test_exists_says_the_creator_is_gone(unique_name: str) -> None:
    """Check that SegmentExistsError says the creator is no longer running when its process has exited."""
    context = mp.get_context("spawn")
    created, attached = context.Event(), context.Event()
    child = context.Process(
        target=create_and_exit, args=(unique_name, created, attached)
    )
    child.start()
    assert created.wait(20)
    # Keeps the segment alive on Windows, where the OS frees it with its last handle.
    other = attach(unique_name)
    attached.set()
    child.join(20)
    assert child.exitcode == 0
    with pytest.raises(SegmentExistsError, match="no longer running") as error:
        create(unique_name)
    assert f"pid {child.pid}" in str(error.value)
    if sys.platform == "win32":
        assert "possibly this one) still has it open" in str(error.value)
        assert "unlink" not in str(error.value)
    else:
        assert "left over from a crash" in str(error.value)
        assert "unlink" in str(error.value)
    other.close()


def test_exists_names_a_creator_that_is_running(unique_name: str) -> None:
    """Check that SegmentExistsError names the creator's pid and says it is still running."""
    segment = create(unique_name)
    with pytest.raises(
        SegmentExistsError, match=rf"pid {os.getpid()}, is still running"
    ):
        create(unique_name)
    segment.close()


@pytest.mark.skipif(sys.platform == "win32", reason="no pid namespaces")
def test_exists_says_the_creator_is_in_another_namespace(unique_name: str) -> None:
    """Check that SegmentExistsError mentions another pid namespace when the stored namespace differs."""
    segment = create(unique_name)
    patch_header(unique_name, 40, "<Q", own_pidns() + 1)
    with pytest.raises(SegmentExistsError, match="another pid namespace"):
        create(unique_name)
    segment.close()


def test_exists_treats_an_unknown_namespace_as_no_information(unique_name: str) -> None:
    """Check that a zero pid namespace produces neither the namespace nor the not-running message."""
    segment = create(unique_name)
    patch_header(unique_name, 40, "<Q", 0)
    with pytest.raises(SegmentExistsError) as error:
        create(unique_name)
    assert "another pid namespace" not in str(error.value)
    assert "no longer running" not in str(error.value)
    segment.close()


def test_exists_says_a_name_without_a_box_may_be_left_over(unique_name: str) -> None:
    """Check that SegmentExistsError for a mapping without a published box says it may be left over."""
    with foreign_mapping(unique_name):
        with pytest.raises(SegmentExistsError, match="holds no published box") as error:
            create(unique_name)
        if sys.platform == "win32":
            assert "possibly this one) still has it open" in str(error.value)
            assert "unlink" not in str(error.value)
        else:
            assert "crash during create" in str(error.value)
            assert "unlink" in str(error.value)


def test_force_unlock_of_a_live_writer_leaves_the_box_usable(unique_name: str) -> None:
    """Check that a writer finishing after force_unlock leaves the box unlocked and usable."""
    writer = create(unique_name, timeout=0.3)
    other = attach(unique_name, timeout=0.3)
    writer._hold_write_lock()
    other.force_unlock()
    other._write([(1, b"forced")])
    generation = other.generation()
    # The slow writer finishes after all; the box must stay unlocked.
    writer._release_held_lock()
    assert writer.generation() >= generation
    other._write([(1, b"after")])
    assert writer._read(1) == b"after"
    other.close()
    writer.close()


@pytest.mark.skipif(
    shm_free_bytes() > 250 << 20,
    reason="needs a /dev/shm smaller than the largest box, as in a container's default 64 MiB",
)
def test_a_box_larger_than_dev_shm_fails_at_create(unique_name: str) -> None:
    """Check that creating a box larger than /dev/shm raises OSError and leaves the name free."""
    fields = [NativeField(i * (4 + (1 << 20)), 1 << 20, BYTES) for i in range(250)]
    size = 250 * (4 + (1 << 20))
    with pytest.raises(OSError):
        Segment.create(
            unique_name, fields, [f"f{i}" for i in range(250)], size, SCHEMA, 1.0, []
        )
    create(unique_name).close()


def test_attach_waits_for_a_creator_that_has_not_made_its_header(
    unique_name: str,
) -> None:
    """Check that an attach racing a create raises only SegmentNotFoundError or succeeds."""
    # The header's named object is found inside the same wait as the magic word,
    # so an attach racing create sees SegmentNotFoundError or a finished segment,
    # never "not a sharedbox".
    errors: list[BaseException] = []
    attached = threading.Event()

    def attach_loop() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                attach(unique_name).close()
            except SegmentNotFoundError:
                continue
            except BaseException as error:
                errors.append(error)
                return
            attached.set()
            return

    thread = threading.Thread(target=attach_loop)
    thread.start()
    segment = create(unique_name)
    thread.join(10)
    segment.close()
    assert not errors
    assert attached.is_set()


def test_a_blocked_read_lets_other_threads_run(unique_name: str) -> None:
    """Check that a read blocked on the lock does not stop other Python threads."""
    segment = create(unique_name, timeout=1.0)
    other = attach(unique_name, timeout=1.0)
    segment._hold_write_lock()
    ticks: list[float] = []
    done = threading.Event()

    def count() -> None:
        while not done.wait(0.01):
            ticks.append(time.monotonic())

    thread = threading.Thread(target=count)
    thread.start()
    start = time.monotonic()
    with pytest.raises(LockTimeoutError):
        other._read(0)
    done.set()
    thread.join()
    assert any(start + 0.3 <= tick <= start + 0.7 for tick in ticks)
    segment.force_unlock()
    other.close()
    segment.close()


def test_close_waits_for_a_read_blocked_on_the_lock(unique_name: str) -> None:
    # The blocked read has released the GIL and takes it back before it returns, so
    # close() must not hold the GIL while it waits for that read. If this test hangs,
    """Check that close waits for a read blocked on the lock, and reads arriving meanwhile raise BoxClosedError."""
    # it does.
    owner = create(unique_name)
    segment = attach(unique_name, timeout=0.5)
    owner._hold_write_lock()
    errors: list[BaseException] = []

    def read() -> None:
        try:
            segment._read(0)
        except BaseException as error:
            errors.append(error)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    time.sleep(0.1)
    closer = threading.Thread(target=segment.close, daemon=True)
    closer.start()
    while not segment.closed:
        time.sleep(0.001)
    # Lets the closer queue on the lock, so this read arrives while close() is pending.
    time.sleep(0.05)
    with pytest.raises(BoxClosedError):
        segment._read(0)
    closer.join(10)
    reader.join(10)
    assert not closer.is_alive() and not reader.is_alive()
    assert [type(error) for error in errors] == [LockTimeoutError]
    owner.force_unlock()
    owner.close()


def test_get_dict_needs_one_name_per_field(unique_name: str) -> None:
    """Check that get_dict maps names to values and raises ValueError when the name count differs."""
    segment = create(unique_name)
    segment._write([(0, struct.pack("<q", 7)), (1, b"hi")])
    assert segment.get_dict(("a", "b")) == {"a": 7, "b": b"hi"}
    with pytest.raises(ValueError, match="1 field names for 2 fields"):
        segment.get_dict(("a",))
    segment.close()


def test_versions_lists_every_field_in_order(unique_name: str) -> None:
    """Check that versions lists each field's write count in field order and raises BoxClosedError once closed."""
    segment = create(unique_name)
    segment._write([(1, b"a")])
    segment._write([(0, bytes(8)), (1, b"b")])
    segment._write([(1, b"c")])
    assert segment.versions() == [1, 3]
    segment.close()
    with pytest.raises(BoxClosedError):
        segment.versions()


def test_an_unpublished_segment_is_attached_only_after_publish(
    unique_name: str,
) -> None:
    """Check that an unpublished segment cannot be attached until publish, and a second publish raises ValueError."""
    segment = Segment.create(
        unique_name, FIELDS, NAMES, RECORD_SIZE, SCHEMA, 1.0, [(0, 7)], publish=False
    )
    with pytest.raises(SegmentNotFoundError):
        attach(unique_name, timeout=0.1)
    with pytest.raises(SegmentExistsError):
        create(unique_name)
    segment.set([(0, 8)])
    segment.publish()
    other = attach(unique_name)
    assert other.get(0) == 8
    with pytest.raises(ValueError, match="already published"):
        segment.publish()
    with pytest.raises(ValueError, match="already published"):
        other.publish()
    other.close()
    segment.close()
    with pytest.raises(BoxClosedError):
        segment.publish()


def test_attach_refuses_a_field_of_a_kind_it_cannot_read(unique_name: str) -> None:
    """Check that attach refuses a segment with a field of an unknown kind, naming the kind."""
    owner = create(unique_name)
    # Field 0 becomes kind 13, which sharedbox.hpp opens as opaque bytes.
    patch_header(unique_name, 132, "<I", 8 | 13 << 24)
    with pytest.raises(
        SchemaMismatchError, match="kind 13, which this version cannot read"
    ):
        attach(unique_name)
    owner.close()


def ref_segment(name: str) -> Segment:
    return Segment.create(
        name, [NativeField(0, 256, REF)], ["Stage.motor"], 256, SCHEMA, 1.0, []
    )


def test_a_reference_is_stored_as_create_id_schema_hash_and_padded_name(
    unique_name: str,
) -> None:
    """Check that a reference is stored as create id, schema hash and zero-padded name, and None as zero bytes."""
    segment = ref_segment(unique_name)
    assert segment.get(0) is None
    segment.set([(0, (7, 0x5EED, "m1"))])
    assert segment._read(0) == struct.pack("<QQ240s", 7, 0x5EED, b"m1")
    assert segment.get(0) == (7, 0x5EED, "m1")
    assert segment.get_dict(("motor",)) == {"motor": (7, 0x5EED, "m1")}
    segment.set([(0, None)])
    assert segment._read(0) == bytes(256)
    assert segment.get(0) is None
    segment.close()


def test_a_reference_name_of_240_bytes_has_no_terminating_nul(unique_name: str) -> None:
    """Check that a box name of 240 bytes fills the name bytes and reads back whole."""
    segment = ref_segment(unique_name)
    segment.set([(0, (1, 2, "n" * 240))])
    assert segment.get(0) == (1, 2, "n" * 240)
    segment.close()


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ((0, 1, "m1"), ValueError),
        ((1, 1, ""), ValueError),
        ((1, 1, "n" * 241), ValueError),
        ((1, 1, "a\x00b"), ValueError),
        ((1, 1, "m\u00f6tor"), ValueError),
        ((1, 1, "bad/name"), ValueError),
        ((-1, 1, "m1"), ValueError),
        ((1, 2**64, "m1"), ValueError),
        (("1", 1, "m1"), TypeError),
        ((1, 1, b"m1"), TypeError),
        ((1, 1), TypeError),
        ("m1", TypeError),
    ],
)
def test_a_reference_value_that_cannot_be_stored_is_refused(
    unique_name: str, value: object, error: type[Exception]
) -> None:
    """Check that a reference value that cannot be stored raises, names the field and leaves the old value."""
    segment = ref_segment(unique_name)
    segment.set([(0, (7, 0x5EED, "m1"))])
    with pytest.raises(error, match="Stage.motor"):
        segment.set([(0, value)])
    assert segment.get(0) == (7, 0x5EED, "m1")
    segment.close()


def test_check_takes_none_for_a_reference() -> None:
    """Check that check() accepts None for a reference field and refuses a value of another type."""
    check(REF, 256, "Stage.motor", None)
    with pytest.raises(TypeError, match="Stage.motor expects a reference, got int"):
        check(REF, 256, "Stage.motor", 3)


def test_cached_ref_returns_the_entry_only_for_the_stored_create_id(
    unique_name: str,
) -> None:
    """Check that cached_ref returns the cached box only for the stored create id and an open segment."""
    segment = ref_segment(unique_name)
    closed = create(f"{unique_name}-c")
    closed.close()
    Segment.unlink(f"{unique_name}-c")
    box = cast(SharedBox, object())
    assert segment.cached_ref(0, {0: (7, box, segment)}) is None
    segment.set([(0, (7, 0x5EED, "m1"))])
    assert segment.cached_ref(0, {}) is False
    assert segment.cached_ref(0, {0: (7, box, segment)}) is box
    assert segment.cached_ref(0, {0: (8, box, segment)}) is False
    assert segment.cached_ref(0, {0: (7, box, closed)}) is False
    segment.close()


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(lambda s: (7, "box"), id="two items"),
        pytest.param(lambda s: (7, "box", s, 0), id="four items"),
        pytest.param(lambda s: ("7", "box", s), id="id not an int"),
        pytest.param(lambda s: (True, "box", s), id="id a bool"),
        pytest.param(lambda s: (-1, "box", s), id="id negative"),
        pytest.param(lambda s: (7, "box", None), id="no segment"),
    ],
)
def test_cached_ref_refuses_an_entry_of_another_shape(
    unique_name: str, entry: Callable[[Segment], tuple[object, ...]]
) -> None:
    """Check that cached_ref raises TypeError for a cache entry that is not (create_id, box, Segment)."""
    segment = ref_segment(unique_name)
    segment.set([(0, (7, 0x5EED, "m1"))])
    with pytest.raises(TypeError, match=r"not \(create_id, box, Segment\)"):
        segment.cached_ref(0, {0: entry(segment)})  # type: ignore[dict-item]
    segment.close()


def test_cached_ref_refuses_a_field_that_is_not_a_reference(unique_name: str) -> None:
    """Check that cached_ref raises ValueError for a field that is not a reference."""
    segment = create(unique_name)
    with pytest.raises(ValueError, match="field 0 is not a reference"):
        segment.cached_ref(0, {})
    segment.close()


def test_attaching_a_stream_name_as_a_box_raises_kind_mismatch(
    unique_name: str,
) -> None:
    """Check that attaching a segment whose magic says stream raises KindMismatchError at once, naming both kinds."""
    owner = create(unique_name)
    with raw_bytes(unique_name) as view:
        view[0:8] = STREAM_MAGIC
    started = time.monotonic()
    with pytest.raises(
        KindMismatchError, match=f"'{unique_name}' is a stream, not a box"
    ) as caught:
        attach(unique_name, 5.0)
    assert time.monotonic() - started < 1.0
    assert isinstance(caught.value, SchemaMismatchError)
    with raw_bytes(unique_name) as view:
        view[0:8] = MAGIC
    owner.close()
