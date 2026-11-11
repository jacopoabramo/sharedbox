import copy
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from bluebox_compare import (
    assert_documents_equal,
    load_documents,
    without_key,
    without_ragged,
)
from event_model.documents import EventDescriptor

from sharedbox import SharedStream
from sharedbox.bluebox._layout import EventLayout

DATA = Path(__file__).parent / "bluebox_data"
Document = tuple[str, dict[str, Any]]


def round_trip(documents: list[Document], **parameters: int) -> list[Document]:
    """Return the events of `documents` after packing and unpacking each."""
    layouts: dict[str, EventLayout] = {}
    result = []
    for name, doc in documents:
        if name == "descriptor":
            layouts[doc["uid"]] = EventLayout.from_descriptor(
                cast(EventDescriptor, doc), **parameters
            )
        elif name == "event":
            layout = layouts[doc["descriptor"]]
            result.append(layout.unpack(layout.pack("event", doc, 1)))
    return result


def events(documents: list[Document]) -> list[Document]:
    """Return the events of `documents`."""
    return [(name, doc) for name, doc in documents if name == "event"]


def descriptor_of(**data_keys: dict[str, object]) -> EventDescriptor:
    """Return a descriptor of `data_keys` with a fresh uid."""
    return cast(
        EventDescriptor,
        {"uid": str(uuid.uuid4()), "name": "primary", "data_keys": data_keys},
    )


def event_of(descriptor: Mapping[str, object], **data: object) -> dict[str, object]:
    """Return an event of `descriptor` holding `data`."""
    return {
        "uid": str(uuid.uuid4()),
        "time": 1.5,
        "seq_num": 1,
        "data": data,
        "timestamps": dict.fromkeys(data, 2.5),
        "filled": {},
        "descriptor": descriptor["uid"],
    }


@pytest.mark.parametrize("path", sorted(DATA.glob("*.json")), ids=lambda p: p.stem)
def test_round_trip_of_every_event(path: Path) -> None:
    """Return every event of each document file unchanged, ragged keys left out."""
    documents = without_ragged(load_documents(path))

    actual = round_trip(documents, string_capacity=256, page_rows=4)

    assert_documents_equal(events(documents), actual)


def test_ragged_shape_is_refused() -> None:
    """Refuse a key with None in its shape, and carry the rest without it."""
    documents = load_documents(DATA / "internal_events.json")
    descriptor = cast(
        EventDescriptor,
        next(doc for name, doc in documents if name == "descriptor"),
    )

    with pytest.raises(ValueError, match="'ragged'"):
        EventLayout.from_descriptor(descriptor, string_capacity=256, page_rows=4)

    rest = without_key(documents, "ragged")
    assert_documents_equal(
        events(rest), round_trip(rest, string_capacity=256, page_rows=4)
    )


def test_missing_filestore_key_is_refused() -> None:
    """Refuse an event without the datum id of a key stored outside the stream."""
    documents = load_documents(DATA / "external_assets_shared_resource.json")
    descriptor = cast(
        EventDescriptor,
        next(doc for name, doc in documents if name == "descriptor"),
    )
    event = copy.deepcopy(next(doc for name, doc in documents if name == "event"))
    del event["data"]["det_image"]
    del event["timestamps"]["det_image"]
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'det_image'"):
        layout.pack("event", event, 1)


def test_page_is_split_and_joined() -> None:
    """Split a page of 40 rows into parts of 16 rows and join them back."""
    descriptor = descriptor_of(
        spectrum={"dtype": "array", "dtype_numpy": "<u2", "shape": [5]},
        count={"dtype": "number", "shape": []},
        label={"dtype": "string", "shape": []},
    )
    rows = 40
    page = {
        "uid": [str(uuid.uuid4()) for _ in range(rows)],
        "time": [1000.0 + row for row in range(rows)],
        "seq_num": list(range(1, rows + 1)),
        "data": {
            "spectrum": np.arange(rows * 5, dtype=np.uint16).reshape(rows, 5),
            "count": [row / 4 for row in range(rows)],
            "label": [f"row {row}" for row in range(rows)],
        },
        "timestamps": {
            key: [2000.0 + row for row in range(rows)]
            for key in ("spectrum", "count", "label")
        },
        "filled": {},
        "descriptor": descriptor["uid"],
    }
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=16)

    items = layout.pack("event_page", page, 1)

    assert [(item.part, item.parts, item.rows) for item in items] == [
        (0, 3, 16),
        (1, 3, 16),
        (2, 3, 8),
    ]
    assert_documents_equal([("event_page", page)], [layout.unpack(items)])


def test_big_endian_key_is_refused() -> None:
    """Refuse a numeric key of big-endian byte order, naming it."""
    descriptor = descriptor_of(
        frame={"dtype": "array", "dtype_numpy": ">u2", "shape": [3]}
    )

    with pytest.raises(ValueError, match="'frame'"):
        EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)


def test_wrong_shape_event_is_refused() -> None:
    """Refuse an event whose array has another shape than its key, naming the key."""
    descriptor = descriptor_of(
        frame={"dtype": "array", "dtype_numpy": "<u2", "shape": [3]}
    )
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'frame'"):
        layout.pack("event", event_of(descriptor, frame=np.zeros(4, np.uint16)), 1)


@pytest.mark.parametrize("value", [2.5, 2**63, float("nan")], ids=repr)
def test_inexact_integer_is_refused(value: float) -> None:
    """Refuse a value an integer key cannot hold exactly, naming the key."""
    descriptor = descriptor_of(count={"dtype": "integer", "shape": []})
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'count'"):
        layout.pack("event", event_of(descriptor, count=value), 1)


@pytest.mark.parametrize("dtype", ["number", "integer", "boolean"])
@pytest.mark.parametrize("value", [None, "1.5", "1"], ids=repr)
def test_value_that_is_not_a_number_is_refused(dtype: str, value: object) -> None:
    """Refuse None or a str for a number, integer or boolean key, naming the key."""
    descriptor = descriptor_of(reading={"dtype": dtype, "shape": []})
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'reading'"):
        layout.pack("event", event_of(descriptor, reading=value), 1)


def test_integral_float_fits_an_integer_key() -> None:
    """Accept 3.0 for an integer key and return it as the integer 3."""
    descriptor = descriptor_of(count={"dtype": "integer", "shape": []})
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    _, doc = layout.unpack(layout.pack("event", event_of(descriptor, count=3.0), 1))

    data = doc["data"]
    assert isinstance(data, dict)
    assert data == {"count": 3}
    assert type(data["count"]) is int


def test_float_fits_a_narrower_float_key() -> None:
    """Accept 0.1 for a float32 key and return the float32 value."""
    descriptor = descriptor_of(
        gain={"dtype": "number", "dtype_numpy": "<f4", "shape": []}
    )
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    _, doc = layout.unpack(layout.pack("event", event_of(descriptor, gain=0.1), 1))

    assert doc["data"] == {"gain": float(np.float32(0.1))}


@pytest.mark.parametrize(
    ("dtype_numpy", "value", "expected"),
    [
        ("<f4", np.float64(0.1), float(np.float32(0.1))),
        ("<i8", np.float64(3.0), 3),
        ("<i4", np.int64(5), 5),
        ("|b1", np.int64(1), True),
    ],
    ids=["float64 in float32", "integral float64", "int64 in int32", "int64 in bool"],
)
def test_numpy_number_converts_as_a_python_one(
    dtype_numpy: str, value: object, expected: object
) -> None:
    """Accept a numpy number where the same Python number converts."""
    descriptor = descriptor_of(
        reading={"dtype": "number", "dtype_numpy": dtype_numpy, "shape": []}
    )
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    _, doc = layout.unpack(layout.pack("event", event_of(descriptor, reading=value), 1))

    assert doc["data"] == {"reading": expected}


@pytest.mark.parametrize(
    ("dtype_numpy", "value"),
    [
        ("<i8", np.float64(3.5)),
        ("<i4", np.int64(2**40)),
        ("|b1", np.int64(2)),
        ("<f8", np.complex128(1j)),
    ],
    ids=["fraction", "too large", "not a bool", "complex"],
)
def test_inexact_numpy_number_is_refused(dtype_numpy: str, value: object) -> None:
    """Refuse a numpy number that does not convert exactly to an integer or bool key, or a complex one for a float key."""
    descriptor = descriptor_of(
        reading={"dtype": "number", "dtype_numpy": dtype_numpy, "shape": []}
    )
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'reading'"):
        layout.pack("event", event_of(descriptor, reading=value), 1)


def test_long_array_without_dtype_numpy_passes() -> None:
    """Carry an array key of 1024 numbers without dtype_numpy, and refuse strings that overflow it."""
    descriptor = descriptor_of(wave={"dtype": "array", "shape": [1024]})
    layout = EventLayout.from_descriptor(descriptor, string_capacity=256, page_rows=1)
    event = event_of(descriptor, wave=[index / 3 for index in range(1024)])

    assert_documents_equal(
        [("event", event)], [layout.unpack(layout.pack("event", event, 1))]
    )
    with pytest.raises(ValueError, match="'wave'.*do not fit"):
        layout.pack("event", event_of(descriptor, wave=["x" * 20] * 1024), 1)


def test_short_filled_column_is_refused() -> None:
    """Refuse a page whose filled column is shorter than its rows, naming the column."""
    descriptor = descriptor_of(
        image={"dtype": "array", "shape": [2], "external": "FILESTORE:"}
    )
    page = {
        "uid": [str(uuid.uuid4()) for _ in range(3)],
        "time": [1.0, 2.0, 3.0],
        "seq_num": [1, 2, 3],
        "data": {"image": ["a", "b", "c"]},
        "timestamps": {"image": [1.0, 2.0, 3.0]},
        "filled": {"image": [False]},
        "descriptor": descriptor["uid"],
    }
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="filled 'image'"):
        layout.pack("event_page", page, 1)


def test_filled_of_many_external_keys_fits() -> None:
    """Carry the filled flags of eight external keys with small capacities."""
    names = [f"detector_{index}_image" for index in range(8)]
    external: dict[str, object] = {
        "dtype": "array",
        "shape": [2],
        "external": "FILESTORE:",
    }
    descriptor = descriptor_of(**dict.fromkeys(names, external))
    event = event_of(descriptor, **{name: str(uuid.uuid4()) for name in names})
    event["filled"] = dict.fromkeys(names, False)
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=1)

    assert_documents_equal(
        [("event", event)], [layout.unpack(layout.pack("event", event, 1))]
    )


def test_unknown_data_key_is_refused() -> None:
    """Refuse an event holding a data key its descriptor does not have, naming it."""
    descriptor = descriptor_of(count={"dtype": "integer", "shape": []})
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'stray'"):
        layout.pack("event", event_of(descriptor, count=1, stray=2.0), 1)


def test_page_of_uneven_rows_is_refused() -> None:
    """Refuse a page whose uid list is shorter than its rows."""
    descriptor = descriptor_of(count={"dtype": "integer", "shape": []})
    page = {
        "uid": [str(uuid.uuid4()) for _ in range(2)],
        "time": [1.0, 2.0, 3.0],
        "seq_num": [1, 2, 3],
        "data": {"count": [1, 2, 3]},
        "timestamps": {"count": [1.0, 2.0, 3.0]},
        "filled": {},
        "descriptor": descriptor["uid"],
    }
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'uid'"):
        layout.pack("event_page", page, 1)


def test_missing_field_is_refused() -> None:
    """Refuse an event without its time, naming the field."""
    descriptor = descriptor_of(count={"dtype": "integer", "shape": []})
    event = event_of(descriptor, count=1)
    del event["time"]
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with pytest.raises(ValueError, match="'time'"):
        layout.pack("event", event, 1)


def test_page_of_strings_at_the_limit_passes() -> None:
    """Carry a full page of strings each exactly string_capacity bytes of UTF-8."""
    descriptor = descriptor_of(label={"dtype": "string", "shape": []})
    rows = 4
    page = {
        "uid": [str(uuid.uuid4()) for _ in range(rows)],
        "time": [1000.0 + row for row in range(rows)],
        "seq_num": list(range(1, rows + 1)),
        "data": {"label": ["é" * 20] * rows},
        "timestamps": {"label": [2000.0] * rows},
        "filled": {},
        "descriptor": descriptor["uid"],
    }
    layout = EventLayout.from_descriptor(descriptor, string_capacity=40, page_rows=rows)

    items = layout.pack("event_page", page, 1)

    assert len(items) == 1
    assert_documents_equal([("event_page", page)], [layout.unpack(items)])


def test_long_string_is_refused() -> None:
    """Refuse a string one byte longer than string_capacity in UTF-8, naming its key."""
    descriptor = descriptor_of(label={"dtype": "string", "shape": []})
    layout = EventLayout.from_descriptor(descriptor, string_capacity=40, page_rows=4)

    with pytest.raises(ValueError, match="'label'.* longer than string_capacity 40"):
        layout.pack("event", event_of(descriptor, label="é" * 20 + "a"), 1)


def test_page_rows_frames_fill_one_item() -> None:
    """Carry page_rows frames in one item and one frame more in two."""
    descriptor = descriptor_of(
        frame={"dtype": "array", "dtype_numpy": "<u2", "shape": [64, 64]}
    )
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=2)

    def page_of(rows: int) -> dict[str, object]:
        return {
            "uid": [str(uuid.uuid4()) for _ in range(rows)],
            "time": [1.0] * rows,
            "seq_num": list(range(1, rows + 1)),
            "data": {"frame": np.ones((rows, 64, 64), np.uint16)},
            "timestamps": {"frame": [1.0] * rows},
            "filled": {},
            "descriptor": descriptor["uid"],
        }

    assert len(layout.pack("event_page", page_of(2), 1)) == 1
    assert len(layout.pack("event_page", page_of(3), 1)) == 2


def test_item_type_makes_a_stream(unique_name: str) -> None:
    """Send a packed event through a stream of the item type and unpack it."""
    descriptor = descriptor_of(
        frame={"dtype": "array", "dtype_numpy": "<u2", "shape": [2, 3]},
        label={"dtype": "string", "shape": []},
        extra={"dtype": "array", "shape": [2]},
    )
    event = event_of(
        descriptor,
        frame=np.arange(6, dtype=np.uint16).reshape(2, 3),
        label="cam",
        extra=["a", 1],
    )
    layout = EventLayout.from_descriptor(descriptor, string_capacity=64, page_rows=4)

    with SharedStream.create(layout.item_type, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            for item in layout.pack("event", event, 1):
                sender.send(item, timeout=5)
        received = [reader.receive(timeout=5)]
        reader.close()
    SharedStream.unlink(unique_name)

    assert_documents_equal([("event", event)], [layout.unpack(received)])
