import contextlib
import mmap
import multiprocessing as mp
import os
import struct
import sys
import threading
import time
from collections.abc import Callable, Generator
from multiprocessing.shared_memory import SharedMemory

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
        shm = SharedMemory(name)
        try:
            assert shm.buf is not None
            yield shm.buf
        finally:
            shm.close()
    else:
        with (
            open(f"/dev/shm/{name}", "r+b") as file,
            mmap.mmap(file.fileno(), 0) as mapping,
            memoryview(mapping) as view,
        ):
            yield view


def patch_header(name: str, offset: int, fmt: str, value: int) -> None:
    with raw_bytes(name) as view:
        header = bytes(view).index(MAGIC)
        struct.pack_into(fmt, view, header + offset, value)


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


def test_small_segment_takes_a_few_pages(unique_name: str) -> None:
    segment = Segment.create(
        unique_name,
        [*FIELDS, NativeField(32, 8, INT)],
        [*NAMES, "c"],
        40,
        SCHEMA,
        1.0,
        [],
    )
    assert segment._size <= 16 * 1024
    segment.close()


@pytest.mark.parametrize(
    ("offset", "fmt", "value"),
    [
        pytest.param(32, "<I", 0xFFFF_FFF0, id="tail-outside"),
        pytest.param(12, "<I", 256, id="tail-too-long"),
        pytest.param(28, "<I", 0xFFFF_FFF0, id="record-outside"),
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


def test_unaligned_tail_is_refused(unique_name: str) -> None:
    segment = create(unique_name)
    with raw_bytes(unique_name) as view:
        header = bytes(view).index(MAGIC)
        (tail,) = struct.unpack_from("<I", view, header + 32)
        entries = struct.pack("<IIII", 0, 8 | INT << 24, 8, 16 | BYTES << 24)
        table = bytes(view).index(entries)
        # A valid field table at the shifted offset, so only the alignment check can refuse it.
        view[table + 4 : table + 4 + len(entries)] = entries
        struct.pack_into("<I", view, header + 32, tail + 4)
    with pytest.raises(SchemaMismatchError, match="corrupt header"):
        attach(unique_name)
    segment.close()


def test_older_layout_is_refused(unique_name: str) -> None:
    segment = create(unique_name)
    patch_header(unique_name, 8, "<I", 1)
    with pytest.raises(SchemaMismatchError, match="uses layout version 1"):
        attach(unique_name)
    segment.close()


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
