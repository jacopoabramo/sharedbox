from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest
from bluebox_compare import assert_documents_equal
from bluebox_devices import ArrayDetector, new_engine, run
from blueboxproc import Child, Document, collect, relay
from bluesky import plans as bp
from ophyd_async.core import init_devices

from sharedbox.bluebox import DocumentSender
from sharedbox.bluebox._channel import remove_channel


@pytest.fixture
def channels(unique_name: str) -> Iterator[tuple[str, str]]:
    """Yield the names of the channel a run is sent on and the channel a relay sends on."""
    names = (unique_name, f"{unique_name}-out")
    yield names
    for name in names:
        remove_channel(name)


def array_detector() -> ArrayDetector:
    with init_devices():
        array = ArrayDetector()
    return array


def test_relay_forwards_unchanged(channels: tuple[str, str]) -> None:
    """Give the last stage of a relay every document the run sent."""
    source, target = channels
    engine = new_engine()
    array = array_detector()
    with (
        DocumentSender.create(source) as sender,
        Child(relay, src=source, dst=target, mode="lossless", transform="same") as middle,
        Child(collect, name=target, mode="lossless") as last,
    ):
        engine.subscribe(sender)
        documents = run(engine, bp.count([array], 20))
        _, middle_ending = middle.result()
        received, last_ending = last.result()

    assert (middle_ending, last_ending) == ("done", "done")
    assert_documents_equal(documents, received)


def test_relay_adds_a_reduced_stream(channels: tuple[str, str]) -> None:
    """Give the last stage the run's documents and an event of sums after each event."""
    source, target = channels
    engine = new_engine()
    array = array_detector()
    with (
        DocumentSender.create(source) as sender,
        Child(relay, src=source, dst=target, mode="lossless", transform="sum") as middle,
        Child(collect, name=target, mode="lossless") as last,
    ):
        engine.subscribe(sender)
        documents = run(engine, bp.count([array], 20))
        middle.result()
        received, ending = last.result()

    assert ending == "done"
    sums = [doc for name, doc in received if name == "descriptor" and doc["name"] == "sums"]
    assert len(sums) == 1
    uid = sums[0]["uid"]
    original = [
        (name, doc)
        for name, doc in received
        if doc.get("uid") != uid and doc.get("descriptor") != uid
    ]
    assert_documents_equal(documents, original)
    descriptors = [i for i, (name, _) in enumerate(received) if name == "descriptor"]
    assert descriptors[1] == descriptors[0] + 1
    reduced = 0
    for index, (name, doc) in enumerate(received):
        if name != "event" or doc["descriptor"] == uid:
            continue
        key = next(iter(doc["data"]))
        follower_name, follower = received[index + 1]
        assert follower_name == "event"
        assert follower["descriptor"] == uid
        assert follower["seq_num"] == doc["seq_num"]
        assert follower["data"] == {key: int(np.sum(doc["data"][key]))}
        reduced += 1
    assert reduced == 20


def test_relay_feeds_two_receivers(channels: tuple[str, str]) -> None:
    """Give a lossless and a lossy receiver behind a relay what each is owed."""
    source, target = channels
    engine = new_engine()
    array = array_detector()
    with (
        DocumentSender.create(source) as sender,
        Child(relay, src=source, dst=target, mode="lossless", transform="same") as middle,
        Child(collect, name=target, mode="lossless") as lossless,
        Child(collect, name=target, mode="lossy", delay=0.02) as lossy,
    ):
        engine.subscribe(sender)
        documents = run(engine, bp.count([array], 60))
        middle.result()
        everything, lossless_ending = lossless.result()
        some, lossy_ending = lossy.result()

    assert (lossless_ending, lossy_ending) == ("done", "done")
    assert_documents_equal(documents, everything)
    events = {doc["uid"]: (name, doc) for name, doc in documents if name == "event"}
    assert_documents_equal(
        [pair for pair in documents if pair[0] != "event"],
        [pair for pair in some if pair[0] != "event"],
    )
    assert_documents_equal(
        [events[doc["uid"]] for name, doc in some if name == "event"],
        [pair for pair in some if pair[0] == "event"],
    )


def test_killed_relay_ends_the_last_stage(channels: tuple[str, str]) -> None:
    """End the last stage with EndOfStream when its relay dies, and let the run reach its stop."""
    source, target = channels
    engine = new_engine()
    array = array_detector()
    with (
        DocumentSender.create(source) as sender,
        Child(relay, src=source, dst=target, mode="lossless", transform="same") as middle,
        Child(collect, name=target, mode="lossless") as last,
    ):
        engine.subscribe(sender)

        def kill_at_ten(name: str, doc: dict[str, Any]) -> None:
            if name == "event" and doc["seq_num"] == 10:
                middle.kill()

        engine.subscribe(kill_at_ten)
        documents = run(engine, bp.count([array], 40))
        received, ending = last.result()

    assert documents[-1][0] == "stop"
    assert ending == "EndOfStream"
    assert 3 <= len(received) < len(documents)
    assert_documents_equal(documents[: len(received)], received)


@pytest.mark.parametrize(
    ("timeout", "relay_ending"),
    [(0.2, "ReceiverReleasedError"), (30, "done")],
    ids=["short", "long"],
)
def test_slow_last_stage(
    channels: tuple[str, str], timeout: float, relay_ending: str
) -> None:
    """Release a relay held back by a slow last stage after the timeout, and hold the run back for 30 s."""
    source, target = channels
    engine = new_engine()
    array = array_detector()
    with (
        DocumentSender.create(
            source,
            event_capacity=2,
            control_timeout=timeout,
            event_timeout=timeout,
            on_full="release",
        ) as sender,
        Child(
            relay,
            src=source,
            dst=target,
            mode="lossless",
            transform="same",
            event_capacity=2,
            control_timeout=timeout,
        ) as middle,
        Child(collect, name=target, mode="lossless", delay=0.4) as last,
    ):
        engine.subscribe(sender)
        documents: list[Document] = run(engine, bp.count([array], 8))
        _, middle_ending = middle.result()
        received, _ = last.result()

    assert documents[-1][0] == "stop"
    assert middle_ending == relay_ending
    if relay_ending == "done":
        assert_documents_equal(documents, received)
