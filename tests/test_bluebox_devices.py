from collections.abc import Callable, Iterator
from pathlib import Path, PurePath
from typing import Any

import pytest
from bluebox_compare import assert_documents_equal, load_documents, without_ragged
from bluebox_devices import ArrayDetector, new_engine, run
from blueboxproc import Child, Document, collect, run_and_die
from bluesky import Msg
from bluesky import plan_stubs as bps
from bluesky import plans as bp
from bluesky.protocols import EventPageCollectable, Flyable
from bluesky.tests.test_external_assets_and_paging import (
    DatumReadable,
    OldDatumCollectable,
    PvPageCollectable,
    StreamDatumReadableCollectable,
    collect_plan,
    collect_plan_no_stream_name_in_collect,
)
from bluesky.tests.test_seq_num_indices_in_sync import (
    ExternalAssetDevice,
    complete,
    describe_collect_with_name,
    kickoff,
)
from ophyd_async import sim
from ophyd_async.core import (
    StaticPathProvider,
    TriggerInfo,
    UUIDFilenameProvider,
    init_devices,
)
from ophyd_async.testing import OneOfEverythingDevice

from sharedbox.bluebox import DocumentSender
from sharedbox.bluebox._channel import remove_channel

DATA = Path(__file__).parent / "bluebox_data"
Plan = Callable[[Path], Any]


@pytest.fixture
def channel(unique_name: str) -> Iterator[str]:
    """Yield a channel name and remove what a killed sender left behind."""
    yield unique_name
    remove_channel(unique_name)


def count_both_detectors(tmp_path: Path) -> Any:
    pattern = sim.PatternGenerator()
    path_provider = StaticPathProvider(UUIDFilenameProvider(), PurePath(tmp_path))
    with init_devices():
        point = sim.SimPointDetector(pattern)
        blob = sim.SimBlobDetector(path_provider, pattern)

    def plan() -> Any:
        yield from bps.mv(point.acquire_time, 0.01)
        yield from bp.count([point, blob], 3)

    return plan()


def scan_a_motor(tmp_path: Path) -> Any:
    pattern = sim.PatternGenerator()
    with init_devices():
        stage = sim.SimStage(pattern)
        point = sim.SimPointDetector(pattern)

    def plan() -> Any:
        yield from bps.mv(point.acquire_time, 0.01, stage.x.velocity, 100)
        yield from bp.scan([point], stage.x, 0, 1, 5)

    return plan()


def fly_a_blob_detector(tmp_path: Path) -> Any:
    path_provider = StaticPathProvider(UUIDFilenameProvider(), PurePath(tmp_path))
    with init_devices():
        blob = sim.SimBlobDetector(path_provider)

    def plan() -> Any:
        yield from bps.stage(blob, wait=True)
        yield from bps.prepare(blob, TriggerInfo(number_of_events=5), wait=True)
        yield from bp.fly([blob], stream_name="primary", collect=True)
        yield from bps.unstage(blob, wait=True)

    return plan()


def count_an_array(tmp_path: Path) -> Any:
    with init_devices():
        array = ArrayDetector()
    return bp.count([array], 5)


def count_one_of_everything(tmp_path: Path) -> Any:
    with init_devices():
        everything = OneOfEverythingDevice()
    return bp.count([everything], 2)


@pytest.mark.parametrize(
    "scenario",
    [
        count_both_detectors,
        scan_a_motor,
        fly_a_blob_detector,
        count_an_array,
        count_one_of_everything,
    ],
    ids=lambda scenario: scenario.__name__,
)
def test_ophyd_async_runs_arrive_unchanged(
    channel: str, tmp_path: Path, scenario: Plan
) -> None:
    """Deliver the documents of a run of ophyd-async devices as the run engine emitted them."""
    engine = new_engine()
    plan = scenario(tmp_path)
    with (
        DocumentSender.create(channel) as sender,
        Child(collect, name=channel, mode="lossless") as receiver,
    ):
        engine.subscribe(sender)
        documents = run(engine, plan)
        received, ending = receiver.result()

    assert ending == "done"
    assert_documents_equal(documents, received)


def count_a_datum_detector() -> Any:
    return bp.count([DatumReadable(name="det")], 2)


def count_a_stream_datum_detector() -> Any:
    return bp.count([StreamDatumReadableCollectable(name="det")], 2)


def collect_event_pages() -> Any:
    return collect_plan_no_stream_name_in_collect(
        PvPageCollectable(name="det"), pre_declare=True, stream_name="main"
    )


def collect_old_style_datum() -> Any:
    return collect_plan(OldDatumCollectable(name="det"), pre_declare=False)


def fly_with_stream_datum() -> Any:
    external_assets = ExternalAssetDevice(10, 100)

    class Flyer(Flyable, EventPageCollectable):
        kickoff = kickoff
        complete = complete
        collect_pages = external_assets.collect_Pageable_with_name  # type: ignore[assignment]
        collect_asset_docs = external_assets.collect_stream_datum
        describe_collect = describe_collect_with_name
        name = "x"

    flyer = Flyer()
    return [
        Msg("open_run", flyer),
        Msg("kickoff", flyer),
        Msg("declare_stream", None, flyer, name="stream1", collect=True),
        *[Msg("collect", flyer, name="stream1")] * 10,
        Msg("complete", flyer),
        Msg("close_run", flyer),
    ]


@pytest.mark.parametrize(
    "scenario",
    [
        count_a_datum_detector,
        count_a_stream_datum_detector,
        collect_event_pages,
        collect_old_style_datum,
        fly_with_stream_datum,
    ],
    ids=lambda scenario: scenario.__name__,
)
def test_bluesky_paged_runs_arrive_unchanged(
    channel: str, scenario: Callable[[], Any]
) -> None:
    """Deliver the event pages, resources and datums of bluesky's test devices unchanged."""
    engine = new_engine()
    plan = scenario()
    with (
        DocumentSender.create(channel) as sender,
        Child(collect, name=channel, mode="lossless") as receiver,
    ):
        engine.subscribe(sender)
        documents = run(engine, plan)
        received, ending = receiver.result()

    assert ending == "done"
    assert_documents_equal(documents, received)


def test_document_files_arrive_unchanged(channel: str) -> None:
    """Deliver the runs of every document file, sent directly and without their ragged keys."""
    paths = sorted(DATA.glob("*.json"))
    documents = [
        pair for path in paths for pair in without_ragged(load_documents(path))
    ]
    with (
        DocumentSender.create(channel) as sender,
        Child(collect, name=channel, mode="lossless", stops=len(paths)) as receiver,
    ):
        for name, doc in documents:
            sender(name, doc)
        received, ending = receiver.result()

    assert ending == "done"
    assert_documents_equal(documents, received)


def control_documents(documents: list[Document]) -> list[Document]:
    """Return the documents that are neither events nor event pages."""
    return [pair for pair in documents if pair[0] not in ("event", "event_page")]


def seq_nums(documents: list[Document]) -> list[int]:
    """Return the sequence numbers of the events of `documents`."""
    return [doc["seq_num"] for name, doc in documents if name == "event"]


def test_modes_keep_every_control_document(channel: str) -> None:
    """Give a lossless receiver every document and a lossy or latest one every control document."""
    engine = new_engine()
    with init_devices():
        array = ArrayDetector()
    with (
        DocumentSender.create(channel) as sender,
        Child(collect, name=channel, mode="lossless") as lossless,
        Child(collect, name=channel, mode="lossy", delay=0.02) as lossy,
        Child(collect, name=channel, mode="latest", delay=0.02) as latest,
    ):
        engine.subscribe(sender)
        documents = run(engine, bp.count([array], 200))
        everything, lossless_ending = lossless.result()
        some, lossy_ending = lossy.result()
        newest, latest_ending = latest.result()

    assert (lossless_ending, lossy_ending, latest_ending) == ("done",) * 3
    assert_documents_equal(documents, everything)
    for received in (some, newest):
        assert_documents_equal(control_documents(documents), control_documents(received))
        numbers = seq_nums(received)
        assert 0 < len(numbers) < 200
        assert numbers == sorted(set(numbers))


def test_two_runs_with_a_lossy_receiver(channel: str) -> None:
    """Give a slow lossy receiver both of two runs run back to back, whole in their control documents."""
    engine = new_engine()
    with init_devices():
        array = ArrayDetector()
    with (
        DocumentSender.create(channel) as sender,
        Child(collect, name=channel, mode="lossy", delay=0.02, stops=2) as lossy,
    ):
        engine.subscribe(sender)
        documents = run(engine, bp.count([array], 100), bp.count([array], 100))
        received, ending = lossy.result()

    assert ending == "done"
    assert_documents_equal(control_documents(documents), control_documents(received))
    assert 0 < len(seq_nums(received)) <= 200


def test_dead_sender_ends_receivers(channel: str) -> None:
    """End a receiver with EndOfStream after the events of a sender that died."""
    with (
        Child(run_and_die, name=channel, duplex=True) as dying,
        Child(collect, name=channel, mode="lossless") as receiver,
    ):
        dying.send("go")
        received, ending = receiver.result()
        dying.result()

    assert ending == "EndOfStream"
    assert [name for name, _ in received] == ["start", "descriptor"] + ["event"] * 10
    assert dying.process.exitcode == 1
