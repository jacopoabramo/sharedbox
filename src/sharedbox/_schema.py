from __future__ import annotations

from ._box import SharedBox
from ._stream import SharedReceiveStream, SharedSendStream, SharedStream, item_of
from ._types import is_box_class


def get_schema_hash(obj: object) -> int:
    """Return the schema hash of a box class or box, a stream or stream end, or a stream item type.

    The schema hash is 64 bits that a segment stores to say how its data
    is laid out, the value C and C++ code compares before reading it:
    `sharedbox::python::take_box`, `take_stream`, `handle::schema_hash` and
    `sbx_schema_hash`. A `SharedBox` subclass or one of its boxes gives the
    box hash. A stream, its sender or a reader gives the hash of its item
    type, and any other object is taken as an item type, such as `int` or
    `tuple[int, Timed[float]]`.

    A box's hash covers the class's identity, the `identity` class keyword
    or by default `module.qualname`, and the name and type of every field
    in declaration order. Changing the identity, which renaming or moving a
    class without an `identity` keyword does, or renaming, reordering,
    adding, removing or retyping a field changes it; defaults and
    documentation do not. A stream's hash covers only the structure of its
    item type, so all streams of one item type share it.

    Raises
    ------
    TypeError
        If `obj` is `SharedBox` itself, or is not a box class, a box, a
        stream or a stream end and no stream can carry it as its item type.
    """
    match obj:
        case _ if obj is SharedBox:
            raise TypeError("SharedBox has no schema hash; pass a SharedBox subclass")
        case SharedBox():
            return type(obj).__layout__.schema_hash
        case SharedStream():
            return obj._item.schema_hash
        case SharedSendStream() | SharedReceiveStream():
            return obj._stream._item.schema_hash
        case _ if is_box_class(obj):
            return obj.__layout__.schema_hash
        case _:
            return item_of(obj).schema_hash
