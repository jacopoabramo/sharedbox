"""The stream item that carries the events of one descriptor."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple

import cbor2
import numpy as np

from .._arrays import DType, MaxShape
from .._types import Capacity
from ._codec import decode_tag_hook, encode_default

if TYPE_CHECKING:
    from event_model.documents import DataKey, Dtype, EventDescriptor


class Key(NamedTuple):
    name: str
    kind: Literal["array", "string", "cbor"]
    dtype: np.dtype | None
    shape: tuple[int, ...]
    capacity: int
    may_be_absent: bool


SCALARS: dict[Dtype, str] = {"number": "float64", "integer": "int64", "boolean": "bool"}
# The longest head CBOR writes before a list, a string or a number.
HEAD = 9
# What a row of a CBOR key may take beyond its elements' heads and content: the
# tag around a typed array, its dtype text and a shape of up to 8 dimensions.
ROW = 128
# Most data keys in one nested record, which holds at most 256 fields.
GROUP = 128
FLOAT64 = np.dtype("float64")
INT64 = np.dtype("int64")


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
        data = cbor2.dumps(value, default=encode_default)
    except (TypeError, cbor2.CBORError) as error:
        raise ValueError(f"{label}: {error}") from None
    if len(data) > capacity:
        raise ValueError(f"{label}: {len(data)} bytes of CBOR do not fit in {capacity}")
    return data


class EventLayout:
    """The stream item of one descriptor, and the conversion of its events to items and back.

    Each data key takes a field `k<i>`, numbered by its position in the
    descriptor, inside the nested record `g<i // 128>`, and column `i` of the
    `timestamps` array. A key with a numeric `dtype_numpy`,
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
        groups: list[type[Any]],
        string_capacity: int,
        page_rows: int,
    ) -> None:
        self.descriptor_uid = descriptor_uid
        self.keys = keys
        self.item_type = item_type
        self.groups = groups
        self.string_capacity = string_capacity
        self.page_rows = page_rows

    @classmethod
    def from_descriptor(
        cls, descriptor: EventDescriptor, *, string_capacity: int, page_rows: int
    ) -> EventLayout:
        """Return the layout of the events of `descriptor`.

        Parameters
        ----------
        string_capacity
            Most bytes of UTF-8 in one string or uid of an event, and the
            bytes each element of a CBOR key with a `dtype_numpy` or no shape
            may take. An element of an array key without `dtype_numpy` may
            take the 9 bytes of a CBOR number.
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
        keys = []
        hints: list[tuple[str, object]] = []
        for index, (name, data_key) in enumerate(descriptor["data_keys"].items()):
            key, hint = cls._key(name, data_key, string_capacity, page_rows)
            keys.append(key)
            hints.append((f"k{index}", hint))
        # A row of `filled` maps each key stored elsewhere to a bool or a datum id.
        filled_row = HEAD + sum(
            2 * HEAD + len(key.name.encode()) + string_capacity
            for key, data_key in zip(
                keys, descriptor["data_keys"].values(), strict=True
            )
            if data_key.get("external")
        )
        try:
            fields += [
                ("uids", Annotated[bytes, Capacity(list_capacity)]),
                ("filled", Annotated[bytes, Capacity(HEAD + page_rows * filled_row)]),
            ]
        except ValueError as error:
            raise ValueError(f"uids and filled: {error}") from None
        fields.append(
            (
                "timestamps",
                Annotated[
                    np.ndarray,
                    MaxShape((page_rows, max(1, len(keys)))),
                    DType("float64"),
                ],
            )
        )
        groups = [
            dataclasses.make_dataclass(
                f"KeyGroup{start // GROUP}", hints[start : start + GROUP], frozen=True
            )
            for start in range(0, len(hints), GROUP)
        ]
        fields += [(f"g{index}", group) for index, group in enumerate(groups)]
        item_type = dataclasses.make_dataclass("EventItem", fields, frozen=True)
        return cls(
            descriptor["uid"], keys, item_type, groups, string_capacity, page_rows
        )

    @staticmethod
    def _key(
        name: str, data_key: DataKey, string_capacity: int, page_rows: int
    ) -> tuple[Key, object]:
        list_capacity = strings_capacity(page_rows, string_capacity)
        external = data_key.get("external")
        sizes = data_key.get("shape", [])
        try:
            if external:
                may_be_absent = external.startswith("STREAM:")
                hint: object = Annotated[bytes, Capacity(list_capacity)]
                return Key(name, "string", None, (), list_capacity, may_be_absent), hint
            shape = tuple(size for size in sizes if size is not None and size != -1)
            if len(shape) != len(sizes):
                raise ValueError(f"shape {sizes} has no fixed size")
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
            # Without a dtype_numpy the elements of an array are most likely
            # numbers; a string among them is refused when an event is packed.
            element = (
                HEAD
                if shape and not data_key.get("dtype_numpy")
                else HEAD + string_capacity
            )
            capacity = HEAD + page_rows * (ROW + elements * element)
            hint = Annotated[bytes, Capacity(capacity)]
            return Key(name, "cbor", None, shape, capacity, False), hint
        except ValueError as error:
            raise ValueError(f"data key {name!r}: {error}") from None

    def pack(self, name: str, doc: Mapping[str, Any], seq: int) -> list[Any]:
        """Return the items that carry the event or event page `doc` as document `seq`.

        An event takes one item. A page takes one item per `page_rows` rows,
        each with its `part` and the number of `parts`.

        Raises
        ------
        ValueError
            If `doc` lacks `uid`, `time`, `seq_num`, `data` or `timestamps`,
            holds a data key the descriptor does not have, or is a page
            whose `uid`, `time` or a column of `filled` has another number of
            rows than `seq_num`.
            If a value has another shape or dtype than its key, is None or
            a str in a numeric key, does not convert (to an integer or bool
            key: exactly; a float key rounds it), is a string longer than
            `string_capacity` bytes of UTF-8, or its CBOR does not fit in
            its field. The message names the key. No item is returned then.
        """
        if name not in ("event", "event_page"):
            raise ValueError(f"cannot pack a {name} document")
        for field in ("uid", "time", "seq_num", "data", "timestamps"):
            if field not in doc:
                raise ValueError(f"{name} document: {field!r} is missing")
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
        else:
            rows = len(doc["seq_num"])
            for field in ("uid", "time"):
                if len(doc[field]) != rows:
                    raise ValueError(
                        f"event_page document: {field!r} has {len(doc[field])} rows, "
                        f"seq_num {rows}"
                    )
            filled = doc.get("filled", {})
            for key, column in filled.items():
                if len(column) != rows:
                    raise ValueError(
                        f"event_page document: filled {key!r} has {len(column)} "
                        f"rows, seq_num {rows}"
                    )
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
        unknown = (data.keys() | timestamps.keys()) - {key.name for key in self.keys}
        if unknown:
            raise ValueError(f"data keys {sorted(unknown)} are not in the descriptor")
        kind = 0 if name == "event" else 1
        self._check_strings("uid", page["uid"])
        seq_num = self._array("seq_num", page["seq_num"], INT64, (), rows)
        time = self._array("time", page["time"], FLOAT64, (), rows)
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
                    FLOAT64,
                    (),
                    rows,
                )
            )
        timestamps_array = (
            np.stack(stamps, axis=1) if stamps else np.zeros((rows, 0), np.float64)
        )
        parts = max(1, math.ceil(rows / self.page_rows))
        list_capacity = strings_capacity(self.page_rows, self.string_capacity)
        items = []
        for part in range(parts):
            rows_of_part = slice(part * self.page_rows, (part + 1) * self.page_rows)
            values: dict[str, object] = {
                "seq": seq,
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
            columns_of_part: list[object] = []
            for key, column in zip(self.keys, columns, strict=True):
                if key.kind == "array":
                    columns_of_part.append(column[rows_of_part])
                else:
                    columns_of_part.append(
                        encode_cbor(
                            f"data key {key.name!r}", column[rows_of_part], key.capacity
                        )
                    )
            values["timestamps"] = timestamps_array[rows_of_part]
            for index, group in enumerate(self.groups):
                start = index * GROUP
                values[f"g{index}"] = group(
                    **{
                        f"k{start + offset}": column
                        for offset, column in enumerate(
                            columns_of_part[start : start + GROUP]
                        )
                    }
                )
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
        # A float key takes any real number and rounds it; an integer or bool
        # key takes a number that converts exactly, checked below.
        if isinstance(column, np.ndarray | np.generic) and not (
            np.can_cast(column.dtype, dtype, "same_kind")
            if dtype.kind in "fc"
            else column.dtype.kind in "biuf"
        ):
            raise ValueError(f"{label}: dtype {column.dtype} is not {dtype}")
        try:
            plain = np.asarray(column)
            # The cast below would turn None into NaN and "1.5" into 1.5.
            if plain.dtype.kind in "OSU":
                for value in plain.flat:
                    if value is None or isinstance(value, str | bytes):
                        raise ValueError(f"{value!r} is not a number")
            with np.errstate(invalid="ignore"):
                array = np.asarray(column, dtype=dtype)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{label}: {error}") from None
        if array.shape != (rows, *shape):
            raise ValueError(f"{label}: shape {array.shape}, not {(rows, *shape)}")
        if dtype.kind in "biu" and not np.array_equal(array, plain):
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
        seq_num = join([item.seq_num for item in items])
        time = join([item.time for item in items])
        data: dict[str, Any] = {}
        timestamps: dict[str, np.ndarray] = {}
        stamps = join([item.timestamps for item in items])
        for index, key in enumerate(self.keys):
            fields = [
                getattr(getattr(item, f"g{index // GROUP}"), f"k{index}")
                for item in items
            ]
            if key.kind == "array":
                column: Any = join(fields)
            else:
                column = [value for field in fields for value in decode_cbor(field)]
                if key.may_be_absent and column and all(v is None for v in column):
                    continue
            data[key.name] = column
            timestamps[key.name] = stamps[:, index]
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


def join(arrays: list[np.ndarray]) -> np.ndarray:
    """Return the rows of `arrays` in one array; one array is returned as it is."""
    return arrays[0] if len(arrays) == 1 else np.concatenate(arrays)


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
