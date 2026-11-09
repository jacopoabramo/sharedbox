import gc
import multiprocessing as mp
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

import pytest

from sharedbox import (
    Attr,
    BoxRef,
    Capacity,
    LockTimeoutError,
    SharedBox,
    attr,
)


class Tally(SharedBox, lock_timeout=5.0, identity="locked/tally"):
    count: int = 0
    state: Annotated[str, Capacity(16)] = ""
    stamp: datetime = datetime(2026, 1, 1, tzinfo=UTC)


class QuickTally(SharedBox, lock_timeout=0.05, identity="locked/tally"):
    count: int = 0
    state: Annotated[str, Capacity(16)] = ""
    stamp: datetime = datetime(2026, 1, 1, tzinfo=UTC)


class BigTally(Tally, identity="locked/big-tally"):
    extra: int = 0


class Dial(SharedBox):
    count: int = 0
    level: Attr[float] = attr()


class Holder(SharedBox):
    tally: Tally | None = None


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
        writer = box._segment.begin_record_write()
        with pytest.raises(ValueError):
            writer.set([(COUNT, 5), (STATE, "x" * 17)])
        assert writer.get_dict(("count", "state", None)) == {"count": 0, "state": ""}
        writer.abort()
        assert (box.count, box.state) == (0, "")


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


def test_dropped_writer_releases_lock(unique_name: str) -> None:
    """Release the lock of a record write that is dropped without end or abort."""
    with Tally.create(unique_name) as box:
        writer = box._segment.begin_record_write()
        writer.set([(COUNT, 5)])
        del writer
        gc.collect()
        start = time.monotonic()
        box.count = 1
        assert time.monotonic() - start < 1.0
        assert box.count == 1


def test_refused_set_then_end_counts_nothing_refused(unique_name: str) -> None:
    """Count only the fields of a later valid set when an earlier set was refused."""
    with Tally.create(unique_name) as box:
        before = box._segment.versions()
        writer = box._segment.begin_record_write()
        with pytest.raises(ValueError):
            writer.set([(COUNT, 5), (STATE, "x" * 17)])
        writer.set([(COUNT, 6)])
        writer.end()
        after = box._segment.versions()
        assert after[COUNT] - before[COUNT] == 1
        assert after[STATE] == before[STATE]
        assert (box.count, box.state) == (6, "")


def increment(box: Tally, times: int) -> None:
    for _ in range(times):
        with box.locked() as b:
            b.count += 1
    box.close()


def read_quickly(name: str, results: "mp.Queue[str]") -> None:
    with QuickTally.attach(name) as box:
        try:
            _ = box.count
        except LockTimeoutError:
            results.put("timeout")
        else:
            results.put("read")


def listen(box: SharedBox, field: str) -> "queue.Queue[tuple[object, object]]":
    seen: queue.Queue[tuple[object, object]] = queue.Queue()
    box.events[field].connect(lambda new, old: seen.put((new, old)))
    return seen


def test_increments_from_two_processes(unique_name: str) -> None:
    """Lose no increment when two processes each add 1000 times inside locked()."""
    context = mp.get_context("spawn")
    with Tally.create(unique_name) as box:
        children = [
            context.Process(target=increment, args=(box, 1000)) for _ in range(2)
        ]
        for child in children:
            child.start()
        for child in children:
            child.join(120)
            assert child.exitcode == 0
        assert box.count == 2000


def test_one_event_per_assigned_field(unique_name: str) -> None:
    """Emit one event per field assigned in the block, with the first old and the last new value."""
    with Tally.create(unique_name) as box:
        counts, states = listen(box, "count"), listen(box, "state")
        with box.locked() as b:
            b.count = 1
            b.count = 2
            b.state = "busy"
        assert counts.get(timeout=5) == (2, 0)
        assert states.get(timeout=5) == ("busy", "")
        time.sleep(0.2)
        assert counts.empty()
        assert states.empty()


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_exception_rolls_back(unique_name: str, error: type[BaseException]) -> None:
    """Restore values and versions, and emit nothing, when the block raises, and re-raise."""
    with Tally.create(unique_name) as box:
        counts, states = listen(box, "count"), listen(box, "state")
        before = box._segment.versions()
        with pytest.raises(error), box.locked() as b:
            b.count = 4
            b.state = "lost"
            raise error
        assert (box.count, box.state) == (0, "")
        assert box._segment.versions() == before
        time.sleep(0.2)
        assert counts.empty()
        assert states.empty()


def test_update_and_snapshot_see_block_writes(unique_name: str) -> None:
    """Show writes made earlier in the block to update and snapshot of the block's object."""
    with Tally.create(unique_name) as box:
        with box.locked() as b:
            b.count = 3
            b.update(state="busy")
            assert b.snapshot() == {
                "count": 3,
                "state": "busy",
                "stamp": datetime(2026, 1, 1, tzinfo=UTC),
            }
            b.count += 1
        assert (box.count, box.state) == (4, "busy")


def test_b_after_block_raises(unique_name: str) -> None:
    """Raise RuntimeError from the block's object once the block has ended."""
    with Tally.create(unique_name) as box:
        with box.locked() as b:
            b.count = 1
        with pytest.raises(RuntimeError, match="has ended"):
            _ = b.count
        with pytest.raises(RuntimeError, match="has ended"):
            b.count = 2
        with pytest.raises(RuntimeError, match="has ended"):
            b.update(count=2)
        with pytest.raises(RuntimeError, match="has ended"):
            b.snapshot()
        assert box.count == 1


def test_b_from_other_thread_raises(unique_name: str) -> None:
    """Raise RuntimeError when another thread uses the block's object."""
    with Tally.create(unique_name) as box, box.locked() as b:
        errors: list[BaseException] = []

        def use() -> None:
            try:
                _ = b.count
            except RuntimeError as error:
                errors.append(error)

        thread = threading.Thread(target=use)
        thread.start()
        thread.join(10)
        assert len(errors) == 1
        assert "another thread" in str(errors[0])


def test_box_in_holding_thread_raises(unique_name: str) -> None:
    """Raise RuntimeError, without waiting, for the box itself used inside its block."""
    with Tally.create(unique_name) as box, box.locked():
        start = time.monotonic()
        with pytest.raises(RuntimeError, match="use the block's object"):
            box.count = 1
        with pytest.raises(RuntimeError, match="use the block's object"):
            _ = box.count
        with pytest.raises(RuntimeError, match="use the block's object"), box.locked():
            pass
        assert time.monotonic() - start < 1.0


@pytest.mark.parametrize(
    ("use", "named"),
    [
        (lambda b: b.close(), "close"),
        (lambda b: b.unlink(), "unlink"),
        (lambda b: b.read_into("count", None), "read_into"),
        (lambda b: b.writing("count"), "writing"),
        (lambda b: b.watch("count"), "watch"),
        (lambda b: b.events, "events"),
        (lambda b: b.force_unlock(), "force_unlock"),
        (lambda b: b.locked(), "locked"),
        (lambda b: b.level, "level"),
        (lambda b: setattr(b, "level", 1.0), "level"),
        (lambda b: b.update(metadata={"a": 1}), "metadata"),
        (lambda b: b.snapshot(follow=True), "follow"),
    ],
    ids=[
        "close",
        "unlink",
        "read_into",
        "writing",
        "watch",
        "events",
        "force_unlock",
        "locked",
        "attr read",
        "attr write",
        "update metadata",
        "snapshot follow",
    ],
)
def test_refused_on_b(
    unique_name: str, use: Callable[[Dial], object], named: str
) -> None:
    """Raise TypeError naming what the block's object does not offer."""
    with Dial.create(unique_name) as box:
        with box.locked() as b:
            with pytest.raises(TypeError, match=named):
                use(b)
            b.count = 1
        assert box.count == 1


def test_reader_times_out_during_long_block(unique_name: str) -> None:
    """Raise LockTimeoutError in another process that reads while the block runs."""
    context = mp.get_context("spawn")
    results: mp.Queue[str] = context.Queue()
    with Tally.create(unique_name) as box:
        child = context.Process(target=read_quickly, args=(unique_name, results))
        with box.locked() as b:
            b.count = 1
            child.start()
            assert results.get(timeout=60) == "timeout"
        child.join(60)
        assert box.count == 1


def test_close_during_block_releases_lock(unique_name: str) -> None:
    """Release the lock at the end of the block although another thread closed the box."""
    with Tally.create(unique_name) as box, Tally.attach(unique_name) as other:
        closer = threading.Thread(target=box.close)
        with box.locked() as b:
            b.count = 1
            closer.start()
            time.sleep(0.2)
        closer.join(10)
        start = time.monotonic()
        other.count = 2
        assert time.monotonic() - start < 1.0
        assert box.closed


def test_reference_field_in_block(
    unique_name: str, names: Callable[[str], str]
) -> None:
    """Read a reference field as a BoxRef and assign it a box inside the block."""
    with (
        Tally.create(names("tally")) as tally,
        Holder.create(unique_name) as holder,
    ):
        with holder.locked() as b:
            assert b.tally is None
            b.tally = tally
            shown: object = b.tally
            assert shown == BoxRef(
                tally.name, Tally.__layout__.schema_hash, tally._segment.create_id
            )
        assert holder.tally is not None
        assert holder.tally.name == tally.name


def test_subclass_gets_its_own_view(
    unique_name: str, names: Callable[[str], str]
) -> None:
    """Give a box class and its subclass each a block object with that class's fields."""
    with (
        BigTally.create(unique_name) as big,
        Tally.create(names("tally")) as plain,
    ):
        with big.locked() as b:
            b.extra += 2
            b.count += 1
        with plain.locked() as b:
            b.count += 1
            with pytest.raises(AttributeError):
                _ = b.extra  # type: ignore[attr-defined]
        assert (big.extra, big.count, plain.count) == (2, 1, 1)


def test_field_named_locked_is_refused() -> None:
    """Refuse a field named locked when the class is defined."""
    with pytest.raises(TypeError, match="locked"):

        class Clash(SharedBox):
            locked: int = 0  # type: ignore[assignment]
