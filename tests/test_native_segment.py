import multiprocessing as mp
import os
import struct
import sys
import threading
import time

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

FIELDS = [NativeField(0, 8, False), NativeField(8, 16, True)]
RECORD_SIZE = 32
SCHEMA = 0x5EED


def create(name: str, timeout: float = 1.0) -> Segment:
    return Segment.create(name, FIELDS, RECORD_SIZE, SCHEMA, timeout)


def attach(name: str, timeout: float = 1.0) -> Segment:
    return Segment.attach(name, SCHEMA, timeout)


def write_pair(name: str, count: int) -> None:
    segment = attach(name)
    for i in range(count):
        segment.write([(0, struct.pack("<q", i)), (1, str(i).encode())])
    segment.close()


def test_attached_segment_sees_writes(unique_name: str) -> None:
    owner = create(unique_name)
    other = attach(unique_name)
    owner.write([(0, struct.pack("<q", 42)), (1, b"hello")])
    assert other.read(0) == struct.pack("<q", 42)
    assert other.read(1) == b"hello"
    assert other.read_all() == [struct.pack("<q", 42), b"hello"]
    other.close()
    owner.close()


def test_new_segment_reads_zero(unique_name: str) -> None:
    segment = create(unique_name)
    assert segment.read_all() == [bytes(8), b""]
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
        Segment.attach(unique_name, SCHEMA + 1, 1.0)
    segment.close()


def test_layout_outside_record_is_rejected(unique_name: str) -> None:
    with pytest.raises(ValueError):
        Segment.create(unique_name, [NativeField(32, 8, False)], RECORD_SIZE, SCHEMA, 1.0)


def test_failed_create_leaves_the_name_free(unique_name: str) -> None:
    with pytest.raises(ValueError):
        Segment.create(unique_name, [NativeField(0, 8, False)], 2**40, SCHEMA, 1.0)
    create(unique_name).close()


def test_oversized_write_changes_nothing(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment.write([(0, struct.pack("<q", 1)), (1, b"x" * 17)])
    assert segment.read(0) == bytes(8)
    assert segment.version(0) == 0
    segment.close()


def test_fixed_field_needs_exact_size(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(ValueError):
        segment.write([(0, b"abc")])
    segment.close()


def test_unknown_field_index(unique_name: str) -> None:
    segment = create(unique_name)
    with pytest.raises(IndexError):
        segment.read(2)
    segment.close()


def test_versions_and_generation_count_writes(unique_name: str) -> None:
    segment = create(unique_name)
    segment.write([(1, b"a")])
    segment.write([(0, bytes(8)), (1, b"b")])
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
    owner.write([(1, b"still mapped")])
    assert owner.read(1) == b"still mapped"
    owner.close()


def test_close_keeps_the_segment_for_others(unique_name: str) -> None:
    owner = create(unique_name)
    other = attach(unique_name)
    owner.write([(1, b"kept")])
    owner.close()
    assert other.read(1) == b"kept"
    other.close()


def test_closed_segment_refuses_use(unique_name: str) -> None:
    segment = create(unique_name)
    segment.close()
    segment.close()
    assert segment.closed
    with pytest.raises(BoxClosedError):
        segment.read(0)


def test_held_lock_times_out_and_force_unlock_recovers(unique_name: str) -> None:
    owner = create(unique_name)
    owner._hold_write_lock()
    other = attach(unique_name, timeout=0.2)
    with pytest.raises(LockTimeoutError, match=str(os.getpid())):
        other.write([(1, b"x")])
    with pytest.raises(LockTimeoutError):
        other.read(1)
    other.force_unlock()
    other.write([(1, b"x")])
    assert other.read(1) == b"x"
    other.close()
    owner.close()


def test_close_while_other_threads_read(unique_name: str) -> None:
    segment = create(unique_name)
    errors: list[BaseException] = []

    def read_until_closed() -> None:
        try:
            while True:
                segment.read_all()
        except BoxClosedError:
            pass
        except BaseException as error:  # noqa: BLE001 (record any unexpected error from the reader thread)
            errors.append(error)

    readers = [threading.Thread(target=read_until_closed) for _ in range(4)]
    for reader in readers:
        reader.start()
    time.sleep(0.2)
    segment.close()
    for reader in readers:
        reader.join(timeout=5)
    assert errors == []
    assert not any(reader.is_alive() for reader in readers)


def test_reads_never_see_half_a_write(unique_name: str) -> None:
    segment = create(unique_name)
    writer = mp.get_context("spawn").Process(target=write_pair, args=(unique_name, 50_000))
    writer.start()
    while writer.is_alive():
        number, text = segment.read_all()
        if text:
            assert str(struct.unpack("<q", number)[0]).encode() == text
    writer.join()
    assert writer.exitcode == 0
    segment.close()
