import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

import pytest

from sharedbox import Capacity, SharedBox


class Tally(SharedBox, lock_timeout=5.0):
    count: int = 0
    state: Annotated[str, Capacity(16)] = ""
    stamp: datetime = datetime(2026, 1, 1, tzinfo=UTC)


COUNT = Tally.__layout__.by_name["count"].index
STATE = Tally.__layout__.by_name["state"].index


def test_end_counts_each_set_field_once(unique_name: str) -> None:
    """Count one write per field set, however often it was set, when the record write ends."""
    with Tally.create(unique_name) as box:
        before = box._segment.versions()
        writer = box._segment.begin_record_write()
        writer.set([(COUNT, 1)])
        writer.set([(COUNT, 2), (STATE, "busy")])
        writer.end()
        after = box._segment.versions()
        assert after[COUNT] - before[COUNT] == 1
        assert after[STATE] - before[STATE] == 1
        assert sum(after) - sum(before) == 2
        assert (box.count, box.state) == (2, "busy")


def test_abort_restores_first_saved_bytes(unique_name: str) -> None:
    """Restore the bytes a field held before its first set when the record write is aborted."""
    with Tally.create(unique_name) as box:
        box.count = 7
        before = box._segment.version(COUNT)
        writer = box._segment.begin_record_write()
        writer.set([(COUNT, 1)])
        writer.set([(COUNT, 2)])
        assert writer.get(COUNT) == 2
        writer.abort()
        assert box.count == 7
        assert box._segment.version(COUNT) == before


def test_set_checks_every_value_first(unique_name: str) -> None:
    """Store nothing from a set whose values do not all fit."""
    with Tally.create(unique_name) as box:
        before = box._segment.versions()
        writer = box._segment.begin_record_write()
        with pytest.raises(ValueError):
            writer.set([(COUNT, 5), (STATE, "x" * 17)])
        assert writer.get_dict(("count", "state", None)) == {"count": 0, "state": ""}
        writer.abort()
        assert (box.count, box.state) == (0, "")
        assert box._segment.versions() == before


@pytest.mark.parametrize(
    "use",
    [
        lambda box: box.count,
        lambda box: box.stamp,
        lambda box: setattr(box, "count", 1),
        lambda box: box.update(count=1),
        lambda box: box.snapshot(),
    ],
    ids=["read", "reused read", "write", "update", "snapshot"],
)
def test_box_read_in_holding_thread_raises(
    unique_name: str, use: Callable[[Tally], object]
) -> None:
    """Raise RuntimeError at once for a read or write of the box by the thread holding its record write."""
    with Tally.create(unique_name) as box:
        _ = box.stamp
        writer = box._segment.begin_record_write()
        start = time.monotonic()
        with pytest.raises(
            RuntimeError, match="is locked by this thread in a locked\\(\\) block"
        ):
            use(box)
        assert time.monotonic() - start < 1.0
        writer.abort()
        assert box.count == 0


def test_writer_after_end_raises(unique_name: str) -> None:
    """Raise RuntimeError from the writer once it has ended, and do nothing on a second end."""
    with Tally.create(unique_name) as box:
        writer = box._segment.begin_record_write()
        writer.end()
        writer.end()
        writer.abort()
        assert writer.ended
        with pytest.raises(RuntimeError, match="^the locked\\(\\) block has ended$"):
            writer.get(COUNT)
        with pytest.raises(RuntimeError, match="^the locked\\(\\) block has ended$"):
            writer.set([(COUNT, 1)])
        assert box.count == 0


def test_metadata_read_in_holding_thread(unique_name: str) -> None:
    """Read the box's metadata head from the thread holding its record write."""
    with Tally.create(unique_name) as box:
        writer = box._segment.begin_record_write()
        box._segment.metadata_head()
        writer.abort()
