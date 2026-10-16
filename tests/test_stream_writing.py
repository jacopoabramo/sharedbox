import dataclasses
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
import pytest

from sharedbox import DType, Shape, SharedStream, StreamClosedError

Image = Annotated[np.ndarray, Shape(4, 4), DType("uint16")]
EMPTY = np.empty((0, 0), np.uint16)


@dataclass(frozen=True)
class Frame:
    index: int
    image: Image


@dataclass(frozen=True)
class Inner:
    image: Image
    label: int


@dataclass(frozen=True)
class Outer:
    index: int
    inner: Inner


@dataclass(frozen=True)
class Tag:
    index: int


@dataclass(frozen=True)
class Pair:
    a: Image
    b: Image


BLANK = Frame(0, EMPTY)


def frame(i: int) -> Frame:
    return Frame(i, np.full((4, 4), i, np.uint16))


@pytest.mark.parametrize(
    ("hint", "template", "kind", "members", "arrays", "expected"),
    [
        (
            Frame,
            Frame(3, EMPTY),
            Frame,
            lambda item: item.index,
            lambda item: [item.image],
            3,
        ),
        (
            Outer,
            Outer(3, Inner(EMPTY, 5)),
            Outer,
            lambda item: (item.index, item.inner.label),
            lambda item: [item.inner.image],
            (3, 5),
        ),
        (
            tuple[Image, int],
            (EMPTY, 3),
            tuple,
            lambda item: item[1],
            lambda item: [item[0]],
            3,
        ),
        (Image, None, np.ndarray, lambda item: None, lambda item: [item], None),
    ],
    ids=["record", "nested", "tuple", "array"],
)
def test_slot_item_is_an_item_over_the_slot(
    unique_name: str,
    hint: Any,
    template: Any,
    kind: type,
    members: Callable[[Any], object],
    arrays: Callable[[Any], list[np.ndarray]],
    expected: object,
) -> None:
    """Build an item of the item type from the template, its other members kept and its arrays writable views of the declared shape."""
    with (
        SharedStream.create(hint, unique_name, capacity=2) as stream,
        stream.sender().writing(template) as slot,
    ):
        assert isinstance(slot.item, kind)
        assert members(slot.item) == expected
        assert [(a.shape, a.flags.writeable) for a in arrays(slot.item)] == [
            ((4, 4), True)
        ]


@pytest.mark.parametrize(
    "template", [None, Outer(0, Inner(EMPTY, 0))], ids=["none", "other-type"]
)
def test_writing_refuses_a_missing_or_wrong_template(
    unique_name: str, template: Any
) -> None:
    """Raise TypeError for no template or one of another type on a record item, and reserve nothing."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        reader = stream.reader()
        sender = stream.sender()
        with pytest.raises(TypeError), sender.writing(template):
            pass
        sender.send(frame(1), timeout=5)
        assert reader.receive(timeout=5).index == 1
        assert reader.position == 0


@pytest.mark.parametrize(
    ("hint", "template", "item"),
    [(int, "seven", 7), (Tag, 7, Tag(1))],
    ids=["int", "record-without-arrays"],
)
def test_writing_refuses_a_template_of_another_type_without_arrays(
    unique_name: str, hint: Any, template: object, item: Any
) -> None:
    """Raise TypeError for a template of another type on an item without arrays, and give the position to the next send."""
    with SharedStream.create(hint, unique_name, capacity=2) as stream:
        reader = stream.reader()
        sender = stream.sender()
        with pytest.raises(TypeError), sender.writing(template, timeout=5):
            pass
        sender.send(item, timeout=5)
        assert reader.receive(timeout=5) == item
        assert reader.position == 0


@pytest.mark.parametrize(
    "pick",
    [lambda image: image, lambda image: image[::-1], lambda image: image + 1],
    ids=["slot", "reversed", "other"],
)
def test_an_item_arrives_whole_whichever_array_it_holds(
    unique_name: str, pick: Callable[[np.ndarray], np.ndarray]
) -> None:
    """Receive the values of the array the item holds: the slot's own, a reversed view of it, or another array."""
    with SharedStream.create(Outer, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender().writing(Outer(0, Inner(EMPTY, 2))) as slot:
            image = slot.item.inner.image
            image[:] = np.arange(16, dtype=np.uint16).reshape(4, 4)
            chosen = pick(image)
            expected = chosen.copy()
            slot.send(dataclasses.replace(slot.item, index=1, inner=Inner(chosen, 2)))
        got = reader.receive(timeout=5)
        np.testing.assert_array_equal(got.inner.image, expected)
        assert (got.index, got.inner.label) == (1, 2)


@pytest.mark.parametrize(
    "pick",
    [
        lambda a, b, outside: (b, a),
        lambda a, b, outside: (outside, a),
    ],
    ids=["swapped", "outside-then-slot"],
)
def test_an_item_arrives_whole_whichever_slot_places_its_arrays_view(
    unique_name: str,
    pick: Callable[[np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]],
) -> None:
    """Receive the values each array held at send when its source is another member's place in the slot."""
    with SharedStream.create(Pair, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender().writing(Pair(EMPTY, EMPTY)) as slot:
            slot.item.a[:] = 1
            slot.item.b[:] = 2
            a, b = pick(slot.item.a, slot.item.b, np.full((4, 4), 3, np.uint16))
            expected = (a.copy(), b.copy())
            slot.send(Pair(a, b))
        got = reader.receive(timeout=5)
        np.testing.assert_array_equal(got.a, expected[0])
        np.testing.assert_array_equal(got.b, expected[1])


def test_a_block_left_without_send_publishes_nothing(unique_name: str) -> None:
    """Publish nothing from a block left without send or by an exception, and give its position to the next send."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        reader = stream.reader()
        sender = stream.sender()
        with sender.writing(BLANK, timeout=5):
            pass
        with pytest.raises(KeyError), sender.writing(BLANK, timeout=5):
            raise KeyError("stop")
        assert stream.statistics().sent == 0
        sender.send(frame(5), timeout=5)
        assert reader.receive(timeout=5).index == 5
        assert reader.position == 0


def test_a_slot_sends_once(unique_name: str) -> None:
    """Raise RuntimeError from a send after one that failed to encode, after abandon, and after one that succeeded."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        sender = stream.sender()
        with sender.writing(BLANK, timeout=5) as slot:
            with pytest.raises(ValueError, match="Shape"):
                slot.send(Frame(0, np.zeros((2, 2), np.uint16)))
            with pytest.raises(RuntimeError):
                slot.send(frame(1))
        with sender.writing(BLANK, timeout=5) as slot:
            slot.abandon()
            with pytest.raises(RuntimeError):
                slot.send(frame(1))
        assert stream.statistics().sent == 0
        with sender.writing(BLANK, timeout=5) as slot:
            slot.send(frame(2))
            with pytest.raises(RuntimeError):
                slot.send(frame(3))
        assert stream.statistics().sent == 1


@pytest.mark.parametrize("call", ["send", "send_nowait", "writing"])
def test_a_send_inside_the_block_raises_runtime_error(
    unique_name: str, call: str
) -> None:
    """Raise RuntimeError, not wait, for a send or writing on the thread that holds the slot."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        sender = stream.sender()
        with sender.writing(BLANK, timeout=5) as slot:
            with pytest.raises(RuntimeError):
                match call:
                    case "send":
                        sender.send(frame(1), timeout=5)
                    case "send_nowait":
                        sender.send_nowait(frame(1))
                    case "writing":
                        with sender.writing(BLANK, timeout=5):
                            pass
            slot.send(frame(0))
        assert stream.statistics().sent == 1


def test_a_send_from_another_thread_waits_for_the_block(unique_name: str) -> None:
    """Publish a send made from another thread during the block after the block's item."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        errors: list[BaseException] = []

        def other() -> None:
            try:
                sender.send(frame(9), timeout=10)
            except BaseException as error:
                errors.append(error)

        with sender.writing(BLANK, timeout=5) as slot:
            thread = threading.Thread(target=other)
            thread.start()
            thread.join(0.2)
            assert thread.is_alive()
            slot.send(frame(1))
        thread.join(10)
        assert errors == []
        assert [reader.receive(timeout=5).index for _ in range(2)] == [1, 9]


def test_closing_the_sender_ends_a_send_waiting_for_the_block(unique_name: str) -> None:
    """Raise StreamClosedError at once in a thread whose send waits for a block when the sender closes."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        sender = stream.sender()
        errors: list[BaseException] = []

        def other() -> None:
            try:
                sender.send(frame(9), timeout=10)
            except BaseException as error:
                errors.append(error)

        with sender.writing(BLANK, timeout=5):
            thread = threading.Thread(target=other)
            thread.start()
            thread.join(0.2)
            sender.close()
            thread.join(0.5)
            assert not thread.is_alive()
        assert [type(error) for error in errors] == [StreamClosedError]


@pytest.mark.parametrize("closing", ["sender", "stream"])
def test_closing_during_the_block_makes_send_raise(
    unique_name: str, closing: str
) -> None:
    """Raise StreamClosedError from slot.send after a close during the block, and keep the item's arrays usable."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        sender = stream.sender()
        with sender.writing(BLANK, timeout=5) as slot:
            (sender if closing == "sender" else stream).close()
            slot.item.image.fill(3)
            assert int(slot.item.image[0, 0]) == 3
            with pytest.raises(StreamClosedError):
                slot.send(slot.item)


def test_writing_raises_timeout_error_while_the_ring_is_full(unique_name: str) -> None:
    """Raise TimeoutError from writing when a lossless reader keeps the ring full past the timeout."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader(start="oldest")
        sender = stream.sender()
        sender.send(0)
        sender.send(1)
        with pytest.raises(TimeoutError), sender.writing(0, timeout=0.1):
            pass
        assert stream.statistics().sent == 2
        assert reader.receive(timeout=5) == 0


def test_a_send_inside_a_block_is_refused_after_another_block_ended(
    unique_name: str,
) -> None:
    """Raise RuntimeError from a send inside either of two threads' blocks, before and after the other block ends."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        sender = stream.sender()
        a_sent = threading.Event()
        b_entered = threading.Event()
        errors: list[BaseException] = []

        def first() -> None:
            try:
                with sender.writing(BLANK, timeout=5) as slot:
                    slot.send(frame(1))
                    a_sent.set()
                    assert b_entered.wait(5)
                    with pytest.raises(RuntimeError):
                        sender.send(frame(9), timeout=1)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=first)
        thread.start()
        assert a_sent.wait(5)
        with sender.writing(BLANK, timeout=5) as slot:
            b_entered.set()
            thread.join(10)
            assert not thread.is_alive()
            with pytest.raises(RuntimeError):
                sender.send(frame(2), timeout=5)
            slot.send(frame(3))
        assert errors == []
