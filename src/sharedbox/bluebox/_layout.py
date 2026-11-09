"""The stream item that carries the events of one descriptor."""

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal, NamedTuple

import cbor2
import numpy as np

from .._arrays import DType, MaxShape
from .._types import Capacity
from ._codec import decode_tag_hook, encode_default


class Key(NamedTuple):
    name: str
    kind: Literal["array", "string", "cbor"]
    dtype: np.dtype | None
    shape: tuple[int, ...]
    capacity: int
    may_be_absent: bool


SCALARS = {"number": "float64", "integer": "int64", "boolean": "bool"}
# The longest head CBOR writes before a list, a string or a number.
HEAD = 9
# What a row of a CBOR key may take beyond its elements' heads and content: the
# tag around a typed array, its dtype text and a shape of up to 8 dimensions.
ROW = 128


def strings_capacity(page_rows: int, string_capacity: int) -> int:
    """Return the bytes of a CBOR list of `page_rows` strings of `string_capacity` bytes."""
    return HEAD + page_rows * (HEAD + string_capacity)


def numeric_dtype(dtype_numpy: object) -> np.dtype | None:
    """Return the dtype a `dtype_numpy` names when an array field can hold it."""
    if not isinstance(dtype_numpy, str):
        return None
    try:
        dtype = np.dtype(dtype_numpy)
    except TypeError:
        return None
    if dtype.kind not in "biufc":
        return None
    if not dtype.isnative:
        raise ValueError(f"dtype_numpy {dtype_numpy!r} is not in native byte order")
    return dtype if dtype.name in DType.__members__ else None


def encode_cbor(label: str, value: object, capacity: int) -> bytes:
    try:
        data = cbor2.dumps(value, default=encode_default, canonical=True)
    except (TypeError, cbor2.CBORError) as error:
        raise ValueError(f"{label}: {error}") from None
    if len(data) > capacity:
        raise ValueError(f"{label}: {len(data)} bytes of CBOR do not fit in {capacity}")
    return data


class EventLayout:
    """The stream item of one descriptor, and the conversion of its events to items and back.

    Each data key takes a field `k<i>` and a timestamp field `t<i>`, numbered
    by its position in the descriptor. A key with a numeric `dtype_numpy`,
    or a `number`, `integer` or `boolean` key without one, is an array with
    a leading dimension of `page_rows`. A `string` key, and a key whose data
    is stored elsewhere (`external` set), is a CBOR list of strings; a
    `STREAM:` key may be absent from an event. Any other key is a CBOR list
    of its values. A page of more than `page_rows` rows takes several items.
    """

    def __init__(
        self,
        descriptor_uid: str,
        keys: list[Key],
        item_type: type[Any],
        string_capacity: int,
        page_rows: int,
    ) -> None:
        self.descriptor_uid = descriptor_uid
        self.keys = keys
        self.item_type = item_type
        self.string_capacity = string_capacity
        self.page_rows = page_rows

    @classmethod
    def from_descriptor(
        cls, descriptor: Mapping[str, Any], *, string_capacity: int, page_rows: int
    ) -> "EventLayout":
        """Return the layout of the events of `descriptor`.

        Parameters
        ----------
        string_capacity
            Most bytes of UTF-8 in one string or uid of an event, and the
            bytes each element of a CBOR key may take.
        page_rows
            Most rows one item holds.

        Raises
        ------
        ValueError
            For a numeric key not in native byte order, a key with `None`
            or `-1` in its shape, or a key whose field would be larger than
            a `sharedbox` field can be. The message names the key.
        """
        list_capacity = strings_capacity(page_rows, string_capacity)
        fields: list[tuple[str, object]] = [
            ("seq", int),
            ("kind", int),
            ("part", int),
            ("parts", int),
            ("rows", int),
            ("seq_num", Annotated[np.ndarray, MaxShape((page_rows,)), DType("int64")]),
            ("time", Annotated[np.ndarray, MaxShape((page_rows,)), DType("float64")]),
        ]
        try:
            fields += [
                ("uids", Annotated[bytes, Capacity(list_capacity)]),
                ("filled", Annotated[bytes, Capacity(list_capacity)]),
            ]
        except ValueError as error:
            raise ValueError(f"uids and filled: {error}") from None
        keys = []
        for index, (name, data_key) in enumerate(descriptor["data_keys"].items()):
            key, hint = cls._key(name, data_key, string_capacity, page_rows)
            keys.append(key)
            fields += [
                (f"k{index}", hint),
                (
                    f"t{index}",
                    Annotated[np.ndarray, MaxShape((page_rows,)), DType("float64")],
                ),
            ]
        item_type = dataclasses.make_dataclass("EventItem", fields, frozen=True)
        return cls(descriptor["uid"], keys, item_type, string_capacity, page_rows)

    @staticmethod
    def _key(
        name: str, data_key: Mapping[str, Any], string_capacity: int, page_rows: int
    ) -> tuple[Key, object]:
        list_capacity = strings_capacity(page_rows, string_capacity)
        external = data_key.get("external")
        shape = data_key.get("shape", [])
        try:
            if external:
                may_be_absent = external.startswith("STREAM:")
                hint: object = Annotated[bytes, Capacity(list_capacity)]
                return Key(name, "string", None, (), list_capacity, may_be_absent), hint
            if any(size is None or size == -1 for size in shape):
                raise ValueError(f"shape {shape} has no fixed size")
            shape = tuple(shape)
            dtype = numeric_dtype(data_key.get("dtype_numpy"))
            if dtype is None and data_key.get("dtype") in SCALARS:
                dtype = np.dtype(SCALARS[data_key["dtype"]])
            if dtype is not None:
                hint = Annotated[
                    np.ndarray, MaxShape((page_rows, *shape)), DType(dtype.name)
                ]
                return Key(name, "array", dtype, shape, 0, False), hint
            if data_key.get("dtype") == "string":
                hint = Annotated[bytes, Capacity(list_capacity)]
                return Key(name, "string", None, shape, list_capacity, False), hint
            elements = max(1, math.prod(shape))
            capacity = HEAD + page_rows * (ROW + elements * (HEAD + string_capacity))
            hint = Annotated[bytes, Capacity(capacity)]
            return Key(name, "cbor", None, shape, capacity, False), hint
        except ValueError as error:
            raise ValueError(f"data key {name!r}: {error}") from None

    def pack(self, name: str, doc: Mapping[str, Any]) -> list[Any]:
        """Return the items that carry the event or event page `doc`.

        An event takes one item. A page takes one item per `page_rows` rows,
        each with its `part` and the number of `parts`. Every item's `seq`
        is 0.

        Raises
        ------
        ValueError
            If a value has another shape or dtype than its key, does not
            convert (to an integer or bool key: exactly; a float key rounds
            it), is a string longer than `string_capacity` bytes of UTF-8,
            or its CBOR does not fit in its field. The message names
            the key. No item is returned then.
        """
        data: Mapping[str, Any]
        timestamps: Mapping[str, Any]
        if name == "event":
            rows = 1
            page = {
                "uid": [doc["uid"]],
                "time": [doc["time"]],
                "seq_num": [doc["seq_num"]],
                "filled": [doc.get("filled", {})],
            }
            data = {key: event_column(value) for key, value in doc["data"].items()}
            timestamps = {key: [value] for key, value in doc["timestamps"].items()}
        elif name == "event_page":
            rows = len(doc["seq_num"])
            filled = doc.get("filled", {})
            page = {
                "uid": list(doc["uid"]),
                "time": doc["time"],
                "seq_num": doc["seq_num"],
                "filled": [
                    {key: values[row] for key, values in filled.items()}
                    for row in range(rows)
                ],
            }
            data = doc["data"]
            timestamps = doc["timestamps"]
        else:
            raise ValueError(f"cannot pack a {name} document")
        kind = 0 if name == "event" else 1
        self._check_strings("uid", page["uid"])
        seq_num = self._array("seq_num", page["seq_num"], np.dtype("int64"), (), rows)
        time = self._array("time", page["time"], np.dtype("float64"), (), rows)
        columns: list[Any] = []
        stamps: list[np.ndarray] = []
        for key in self.keys:
            if key.name not in data and key.may_be_absent:
                columns.append([None] * rows)
                stamps.append(np.full(rows, np.nan))
                continue
            if key.name not in data or key.name not in timestamps:
                raise ValueError(f"data key {key.name!r} is missing")
            label = f"data key {key.name!r}"
            column = data[key.name]
            if key.kind == "array":
                assert key.dtype is not None
                columns.append(self._array(label, column, key.dtype, key.shape, rows))
            else:
                columns.append(self._values(label, key, column, rows))
            stamps.append(
                self._array(
                    f"timestamp of {label}",
                    timestamps[key.name],
                    np.dtype("float64"),
                    (),
                    rows,
                )
            )
        parts = max(1, math.ceil(rows / self.page_rows))
        list_capacity = strings_capacity(self.page_rows, self.string_capacity)
        items = []
        for part in range(parts):
            rows_of_part = slice(part * self.page_rows, (part + 1) * self.page_rows)
            values: dict[str, object] = {
                "seq": 0,
                "kind": kind,
                "part": part,
                "parts": parts,
                "rows": len(seq_num[rows_of_part]),
                "seq_num": seq_num[rows_of_part],
                "time": time[rows_of_part],
                "uids": encode_cbor("uids", page["uid"][rows_of_part], list_capacity),
                "filled": encode_cbor(
                    "filled", page["filled"][rows_of_part], list_capacity
                ),
            }
            for index, (key, column, stamp) in enumerate(
                zip(self.keys, columns, stamps, strict=True)
            ):
                if key.kind == "array":
                    values[f"k{index}"] = column[rows_of_part]
                else:
                    values[f"k{index}"] = encode_cbor(
                        f"data key {key.name!r}", column[rows_of_part], key.capacity
                    )
                values[f"t{index}"] = stamp[rows_of_part]
            items.append(self.item_type(**values))
        return items

    def _array(
        self,
        label: str,
        column: object,
        dtype: np.dtype,
        shape: tuple[int, ...],
        rows: int,
    ) -> np.ndarray:
        if isinstance(column, np.ndarray | np.generic) and not np.can_cast(
            column.dtype, dtype, "safe"
        ):
            raise ValueError(f"{label}: dtype {column.dtype} is not {dtype}")
        try:
            array = np.asarray(column, dtype=dtype)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{label}: {error}") from None
        if array.shape != (rows, *shape):
            raise ValueError(f"{label}: shape {array.shape}, not {(rows, *shape)}")
        # Devices report the values of a float32 key as Python floats, so a
        # float key rounds; an integer or bool key would change the value.
        if (
            dtype.kind in "biu"
            and not isinstance(column, np.ndarray | np.generic)
            and not np.array_equal(array, np.asarray(column))
        ):
            raise ValueError(f"{label}: a value does not convert to {dtype} exactly")
        return array

    def _values(
        self, label: str, key: Key, column: Iterable[object], rows: int
    ) -> list[object]:
        try:
            values = list(column)
        except TypeError:
            raise ValueError(f"{label}: {column!r} is not a list of rows") from None
        if len(values) != rows:
            raise ValueError(f"{label}: {len(values)} rows, not {rows}")
        if key.kind == "string":
            self._check_strings(label, values)
        return values

    def _check_strings(self, label: str, values: Iterable[object]) -> None:
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"{label}: {value!r} is not a str")  # noqa: TRY004
            size = len(value.encode())
            if size > self.string_capacity:
                raise ValueError(
                    f"{label}: a string of {size} bytes of UTF-8 is longer than "
                    f"string_capacity {self.string_capacity}"
                )

    def unpack(self, items: Sequence[Any]) -> tuple[str, dict[str, object]]:
        """Return the event or event page that the items of one document carry."""
        uids = [uid for item in items for uid in decode_cbor(item.uids)]
        filled = [row for item in items for row in decode_cbor(item.filled)]
        seq_num = np.concatenate([item.seq_num for item in items])
        time = np.concatenate([item.time for item in items])
        data: dict[str, Any] = {}
        timestamps: dict[str, np.ndarray] = {}
        for index, key in enumerate(self.keys):
            stamps = np.concatenate([getattr(item, f"t{index}") for item in items])
            if key.kind == "array":
                column: Any = np.concatenate(
                    [getattr(item, f"k{index}") for item in items]
                )
            else:
                column = [
                    value
                    for item in items
                    for value in decode_cbor(getattr(item, f"k{index}"))
                ]
                if key.may_be_absent and column and all(v is None for v in column):
                    continue
            data[key.name] = column
            timestamps[key.name] = stamps
        if items[0].kind == 0:
            return "event", {
                "uid": uids[0],
                "time": time[0].item(),
                "seq_num": seq_num[0].item(),
                "data": {key: first_row(value) for key, value in data.items()},
                "timestamps": {
                    key: value[0].item() for key, value in timestamps.items()
                },
                "filled": filled[0],
                "descriptor": self.descriptor_uid,
            }
        return "event_page", {
            "uid": uids,
            "time": time.tolist(),
            "seq_num": seq_num.tolist(),
            "data": data,
            "timestamps": {key: value.tolist() for key, value in timestamps.items()},
            "filled": {
                key: [row[key] for row in filled]
                for key in (filled[0] if filled else {})
            },
            "descriptor": self.descriptor_uid,
        }


def decode_cbor(data: bytes) -> list[Any]:
    values: list[Any] = cbor2.loads(data, tag_hook=decode_tag_hook)
    return values


def event_column(value: object) -> object:
    """Return an event's value as a column of one row."""
    if isinstance(value, np.ndarray | np.generic):
        return np.expand_dims(value, 0)
    return [value]


def first_row(column: object) -> object:
    if isinstance(column, np.ndarray):
        row = column[0]
        return row.item() if row.ndim == 0 else row
    assert isinstance(column, list)
    return column[0]
