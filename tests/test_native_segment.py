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

import pytest

from sharedbox._layout import NativeField
from sharedbox._native import (
    BoxClosedError,
    LockTimeoutError,
    SchemaMismatchError,
    Segment,
    SegmentExistsError,
    SegmentNotFoundError,
)

BOOL, INT, FLOAT, STR, BYTES = range(5)
FIELDS = [NativeField(0, 8, INT), NativeField(8, 16, BYTES)]
NAMES = ["a", "b"]
RECORD_SIZE = 32
SCHEMA = 0x5EED


def create(name: str, timeout: float = 1.0) -> Segment:
    return Segment.create(name, FIELDS, NAMES, RECORD_SIZE, SCHEMA, timeout, [])


def attach(name: str, timeout: float = 1.0) -> Segment:
    return Segment.attach(name, NAMES, SCHEMA, timeout)


MAGIC = b"SHREDBX1"


@contextlib.contextmanager
def raw_bytes(name: str) -> Generator[memoryview, None, None]:
    if sys.platform == "win32":
        shm = SharedMemory(f"sharedbox.{name}")
        try:
            assert shm.buf is not None
            yield shm.buf
        finally:
            shm.close()
    else:
        with (
            open(f"/dev/shm/sharedbox.{name}", "r+b") as file,
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
        with mmap.mmap(-1, 4096, tagname=f"sharedbox.{name}"):
            yield
    else:
        fd = os.open(
            f"/dev/shm/sharedbox.{name}", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
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
    owner = create(unique_name)
    other = attach(unique_name)
    owner._write([(0, struct.pack("<q", 42)), (1, b"hello")])
    assert other._read(0) == struct.pack("<q", 42)
    assert other._read(1) == b"hello"
    assert other._read_all() == [struct.pack("<q", 42), b"hello"]
    other.close()
    owner.close()


def test_new_segment_reads_zero(unique_name: str) -> None:
    segment = create(unique_name)
    assert segment._read_all() == [bytes(8), b""]
    segment.close()


def test_create_refuses_existing_name(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(SegmentExistsError):
        create(unique_name)
    segment.close()


def test_attach_to_missing_name(unique_name: str) -> None:
    with pytest.raises(SegmentNotFoundError):
        attach(unique_name)


def test_attach_with_other_schema(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(SchemaMismatchError):
        Segment.attach(unique_name, NAMES, SCHEMA + 1, 1.0)
    segment.close()


def test_layout_outside_record_is_rejected(unique_name: str) -> None:
    with pytest.raises(ValueError):
        Segment.create(
            unique_name, [NativeField(32, 8, INT)], ["a"], RECORD_SIZE, SCHEMA, 1.0, []
        )


def test_failed_create_leaves_the_name_free(unique_name: str) -> None:
    with pytest.raises(ValueError):
        Segment.create(
            unique_name, [NativeField(0, 8, INT)], ["a"], 2**40, SCHEMA, 1.0, []
        )
    create(unique_name).close()


def test_oversized_write_changes_nothing(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment._write([(0, struct.pack("<q", 1)), (1, b"x" * 17)])
    assert segment._read(0) == bytes(8)
    assert segment.version(0) == 0
    segment.close()


def test_fixed_field_needs_exact_size(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment._write([(0, b"abc")])
    segment.close()


def test_unknown_field_index(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(IndexError):
        segment._read(2)
    segment.close()


def test_versions_and_generation_count_writes(unique_name: str) -> None:
    segment = create(unique_name)
    segment._write([(1, b"a")])
    segment._write([(0, bytes(8)), (1, b"b")])
    assert (segment.version(0), segment.version(1), segment.generation()) == (1, 2, 2)
    segment.close()


def test_unlink_removes_the_name_like_shared_memory(unique_name: str) -> None:
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
    owner = create(unique_name)
    other = attach(unique_name)
    owner._write([(1, b"kept")])
    owner.close()
    assert other._read(1) == b"kept"
    other.close()


def test_closed_segment_refuses_use(unique_name: str) -> None:
    segment = create(unique_name)
    segment.close()
    segment.close()
    assert segment.closed
    with pytest.raises(BoxClosedError):
        segment._read(0)


def test_held_lock_times_out_and_force_unlock_recovers(unique_name: str) -> None:
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
    segment = create(unique_name)
    errors: list[BaseException] = []

    def until_closed(call: Callable[[], object]) -> None:
        try:
            while True:
                call()
        except BoxClosedError:
            pass
        except BaseException as error:  # noqa: BLE001 (record any unexpected error from the thread)
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
    with pytest.raises(ValueError):
        create(unique_name, timeout)
    segment = create(unique_name)
    with pytest.raises(ValueError):
        attach(unique_name, timeout)
    segment.close()


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), -1.0, 86401.0])
def test_bad_wait_timeout_is_refused(unique_name: str, timeout: float) -> None:
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment.wait(segment.generation(), timeout)
    segment.close()


def test_small_box_takes_one_page(unique_name: str) -> None:
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


def test_raw_bytes_follow_layout_1_0(unique_name: str) -> None:
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
    assert raw[8:12] == bytes([1, 0, 0, 0])
    assert struct.unpack_from("<HHQIIII", raw, 12) == (
        3,
        64,
        0x1122334455667788,
        24,
        1728,
        128,
        4096,
    )
    create_id, creator_start, creator_pid = struct.unpack_from("<QQI", raw, 40)
    assert create_id != 0
    assert creator_start != 0
    assert creator_pid == os.getpid()
    assert raw[60:64] == bytes(4)
    # seq, writer_pid, wake_word and waiters after one write, with no one waiting.
    assert struct.unpack_from("<QIII", raw, 64) == (2, 0, 1, 0)
    assert raw[84:88] == bytes(4)
    assert struct.unpack_from("<Q", raw, 88) == (own_pidns(),)
    assert raw[96:128] == bytes(32)
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
        pytest.param(12, "<H", 0, id="no-fields"),
        pytest.param(12, "<H", 256, id="table-over-the-record"),
        pytest.param(14, "<H", 0, id="no-waiter-slots"),
        pytest.param(14, "<H", 4097, id="too-many-waiter-slots"),
        pytest.param(24, "<I", 4000, id="record-past-the-mapping"),
        pytest.param(28, "<I", 0xFFFF_FFC0, id="record-outside"),
        pytest.param(28, "<I", 128, id="record-over-the-table"),
        pytest.param(28, "<I", 1736, id="record-unaligned"),
        pytest.param(32, "<I", 136, id="tail-moved"),
        pytest.param(36, "<I", 8192, id="size-differs"),
        pytest.param(128, "<I", 32, id="field-past-the-record"),
        pytest.param(132, "<I", 8 | 9 << 24, id="unknown-kind"),
    ],
)
def test_corrupt_header_is_refused(
    unique_name: str, offset: int, fmt: str, value: int
) -> None:
    segment = create(unique_name)
    patch_header(unique_name, offset, fmt, value)
    with pytest.raises(SchemaMismatchError, match="corrupt header"):
        attach(unique_name)
    segment.close()


def test_another_major_version_is_refused(unique_name: str) -> None:
    segment = create(unique_name)
    patch_header(unique_name, 8, "<H", 2)
    with pytest.raises(SchemaMismatchError, match=r"uses layout 2\.0"):
        attach(unique_name)
    segment.close()


def test_a_higher_minor_version_opens(unique_name: str) -> None:
    owner = create(unique_name)
    patch_header(unique_name, 10, "<H", 7)
    other = attach(unique_name)
    owner._write([(1, b"minor")])
    assert other._read(1) == b"minor"
    other.close()
    owner.close()


def test_a_name_at_the_length_limit(unique_name: str) -> None:
    name = (unique_name + "x" * 128)[:128]
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
    with foreign_mapping(unique_name):
        with pytest.raises(SegmentExistsError):
            create(unique_name)
        with pytest.raises(SegmentNotFoundError):
            attach(unique_name, timeout=0.2)


def test_a_writer_killed_holding_the_lock(unique_name: str) -> None:
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
    segment = create(unique_name)
    with pytest.raises(
        SegmentExistsError, match=rf"pid {os.getpid()}, is still running"
    ):
        create(unique_name)
    segment.close()


@pytest.mark.skipif(sys.platform == "win32", reason="no pid namespaces")
def test_exists_says_the_creator_is_in_another_namespace(unique_name: str) -> None:
    segment = create(unique_name)
    patch_header(unique_name, 88, "<Q", own_pidns() + 1)
    with pytest.raises(SegmentExistsError, match="another pid namespace"):
        create(unique_name)
    segment.close()


def test_exists_treats_an_unknown_namespace_as_no_information(unique_name: str) -> None:
    segment = create(unique_name)
    patch_header(unique_name, 88, "<Q", 0)
    with pytest.raises(SegmentExistsError) as error:
        create(unique_name)
    assert "another pid namespace" not in str(error.value)
    assert "no longer running" not in str(error.value)
    segment.close()


def test_exists_says_a_name_without_a_box_may_be_left_over(unique_name: str) -> None:
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
            except BaseException as error:  # noqa: BLE001
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
    # it does.
    owner = create(unique_name)
    segment = attach(unique_name, timeout=0.5)
    owner._hold_write_lock()
    errors: list[BaseException] = []

    def read() -> None:
        try:
            segment._read(0)
        except BaseException as error:  # noqa: BLE001
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
