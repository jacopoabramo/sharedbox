"""Opens boxes whose header, field table or slot table holds random bytes; run by test_properties_forged.

Runs in its own process so that a crash, which would take down a pytest worker, shows up as an exit
code. The one argument is the number of examples to try.
"""

import contextlib
import mmap
import sys
import uuid
from collections.abc import Generator
from multiprocessing.shared_memory import SharedMemory

from hypothesis import example, given, settings
from hypothesis import strategies as st
from os_names import os_name

from sharedbox._layout import NativeField
from sharedbox._native import (
    LockTimeoutError,
    SchemaMismatchError,
    Segment,
    SegmentNotFoundError,
)

BOOL, INT, FLOAT, STR, BYTES = range(5)
FIELDS = [
    NativeField(0, 8, INT),
    NativeField(8, 8, FLOAT),
    NativeField(16, 12, STR),
    NativeField(32, 8, BYTES),
    NativeField(44, 1, BOOL),
]
NAMES = ["a", "b", "c", "d", "e"]
SCHEMA = 0xF0F0
SLOTS = 4
# (start, length) of header lines 0 and 1, the field table and the waiter slot table.
REGIONS = [
    (0, 64),
    (64, 64),
    (128, len(FIELDS) * 8),
    (128 + len(FIELDS) * 16, SLOTS * 24),
]


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


EDITS = st.lists(
    st.tuples(st.sampled_from(REGIONS), st.integers(0, 4095), st.integers(0, 255)),
    min_size=1,
    max_size=12,
)


def forged_open(edits: list[tuple[tuple[int, int], int, int]]) -> None:
    name = f"sbforge-{uuid.uuid4().hex[:16]}"
    owner = Segment.create(
        name, FIELDS, NAMES, 48, SCHEMA, 1.0, [(0, 7), (2, "kept")], SLOTS
    )
    try:
        with raw_bytes(name) as view:
            for (start, length), offset, value in edits:
                view[start + offset % length] = value
        try:
            other = Segment.attach(name, NAMES, SCHEMA, 0.05)
        except (SchemaMismatchError, SegmentNotFoundError):
            return
        # A box that opens is read and written through its own checked copy of the table.
        try:
            other.get_dict(tuple(NAMES))
            other.set([(0, 1)])
        except LockTimeoutError:
            # A forged seq or writer_pid looks like a writer that never finishes.
            pass
        finally:
            other.close()
    finally:
        owner.close()
        with contextlib.suppress(SegmentNotFoundError):
            Segment.unlink(name)


def main(examples: int) -> None:
    # A field count lowered to one the mapping still fits passes every geometry check.
    pinned = example([((64, 64), 20, 1)])
    given(EDITS)(
        pinned(
            settings(max_examples=examples, deadline=None, database=None)(forged_open)
        )
    )()


if __name__ == "__main__":
    main(int(sys.argv[1]))
