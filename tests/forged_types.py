"""Opens boxes whose description table or record holds random bytes; run by test_properties_forged.

Runs in its own process so that a crash, which would take down a pytest worker, shows up as an exit
code. The one argument is the number of examples to try.
"""

# ruff: noqa: DTZ001, RUF012
import contextlib
import dataclasses
import datetime
import decimal
import enum
import struct
import sys
import uuid
from typing import Annotated, Literal

from forged_open import raw_bytes
from hypothesis import given, settings
from hypothesis import strategies as st

from sharedbox import Capacity, SharedBox
from sharedbox._native import SchemaMismatchError, Segment, SegmentNotFoundError


class Colour(enum.Enum):
    RED = 1
    GREEN = 2


@dataclasses.dataclass
class Inner:
    when: datetime.datetime
    tags: Annotated[list[int], Capacity(3)]


class Forged(SharedBox):
    colour: Colour = Colour.RED
    mode: Literal["a", "b"] = "a"
    maybe: Annotated[str | None, Capacity(4)] = None
    either: int | bool = 0
    inner: Inner = Inner(datetime.datetime(2026, 1, 1), [1])
    scores: Annotated[dict[Annotated[str, Capacity(4)], float], Capacity(2)] = {}
    price: Annotated[decimal.Decimal, Capacity(8)] = decimal.Decimal(1)


LAYOUT = Forged.__layout__
LABELS = [spec.label for spec in LAYOUT.fields]


def regions(view: memoryview) -> list[tuple[int, int]]:
    """(start, length) of the description table and the record, read from the header."""
    (slots,) = struct.unpack_from("<H", view, 52)
    field_count = struct.unpack_from("<H", view, 88)[0]
    record_size, record = struct.unpack_from("<II", view, 92)
    (types_size,) = struct.unpack_from("<I", view, 104)
    table = 128 + field_count * 16 + slots * 24
    return [(table, types_size), (record, record_size)]


EDITS = st.lists(
    st.tuples(st.integers(0, 1), st.integers(0, 1 << 20), st.integers(0, 255)),
    min_size=1,
    max_size=12,
)


def forged_types(edits: list[tuple[int, int, int]]) -> None:
    name = f"sbforge-{uuid.uuid4().hex[:16]}"
    owner = Forged.create(name)
    try:
        with raw_bytes(name) as view:
            places = regions(view)
            for region, offset, value in edits:
                start, length = places[region]
                view[start + offset % length] = value
        try:
            other = Segment.attach(name, LABELS, LAYOUT.schema_hash, 0.05, LAYOUT.types)
        except (SchemaMismatchError, SegmentNotFoundError):
            return
        # A box that opens reads each field through checks that raise, never crash.
        for spec in LAYOUT.fields:
            with contextlib.suppress(ValueError, TypeError, ArithmeticError):
                other.get(spec.index, LAYOUT.types)
        with contextlib.suppress(ValueError, TypeError, ArithmeticError):
            other.get_dict(LAYOUT.names, LAYOUT.types)
        other.close()
    finally:
        owner.close()
        with contextlib.suppress(SegmentNotFoundError):
            Forged.unlink(name)


def main(examples: int) -> None:
    given(EDITS)(
        settings(max_examples=examples, deadline=None, database=None)(forged_types)
    )()


if __name__ == "__main__":
    main(int(sys.argv[1]))
