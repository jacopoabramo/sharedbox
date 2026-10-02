import datetime
import decimal
import sys
import typing
import uuid
from typing import Annotated, Final, NewType

import pytest
from crossproc import snapshot_in_child, update_in_child

from sharedbox import Capacity, SharedBox
from sharedbox._layout import build_layout

UserId = NewType("UserId", int)
PLUS_ONE = datetime.timezone(datetime.timedelta(hours=1))


class Event(SharedBox):
    when: datetime.datetime
    day: datetime.date
    at: datetime.time
    span: datetime.timedelta
    uid: uuid.UUID
    z: complex
    price: Annotated[decimal.Decimal, Capacity(16)]
    raw: Annotated[bytearray, Capacity(8)]
    user: UserId
    limit: Final[int] = 10


VALUES = {
    "when": datetime.datetime(2026, 10, 2, 9, 30, tzinfo=PLUS_ONE),
    "day": datetime.date(2026, 10, 2),
    "at": datetime.time(9, 30, fold=1),
    "span": datetime.timedelta(days=-1, seconds=5),
    "uid": uuid.UUID(int=12345),
    "z": complex(0.5, -1),
    "price": decimal.Decimal("19.99"),
    "raw": bytearray(b"ab"),
    "user": UserId(7),
}


def test_scalar_fields_survive_another_process(unique_name: str) -> None:
    """Check that values written here read back equal, with the same types, in a spawned process and after its update."""
    with Event.create(unique_name, **VALUES) as box:
        seen = snapshot_in_child(box)
        assert seen == {**VALUES, "limit": 10}
        assert type(seen["raw"]) is bytearray
        assert seen["when"].utcoffset() == datetime.timedelta(hours=1)
        update_in_child(
            box, day=datetime.date(2000, 1, 1), price=decimal.Decimal("0.5")
        )
        assert box.day == datetime.date(2000, 1, 1)
        assert box.price == decimal.Decimal("0.5")
        assert type(box.raw) is bytearray
        box.limit = 11  # type: ignore[misc]
        assert box.limit == 11
    Event.unlink(unique_name)


def test_a_datetime_is_refused_by_a_date_field(unique_name: str) -> None:
    """Check that a datetime given to a date field raises TypeError, rather than storing the date alone."""
    with Event.create(unique_name, **VALUES) as box:
        with pytest.raises(TypeError, match="Event.day expects a date"):
            box.day = datetime.datetime(2026, 1, 1, 12)
        assert box.day == VALUES["day"]
    Event.unlink(unique_name)


def test_classes_with_only_0_3_kinds_keep_their_schema_hash() -> None:
    """Check that a class with only bool, int, float and str fields hashes as it did in 0.3."""
    motor = type(
        "Motor",
        (),
        {
            "__annotations__": {
                "position": int,
                "enabled": bool,
                "label": Annotated[str, Capacity(32)],
            },
            "__module__": "__main__",
        },
    )
    assert build_layout(motor).schema_hash == 0x82CE467598596A72


@pytest.mark.parametrize(
    ("hint", "message"),
    [
        (str, "unsupported annotation"),
        (Annotated[int, Capacity(4)], "Capacity does not apply"),
        (Annotated[str, Capacity(4), Capacity(8)], "more than one Capacity"),
        (Annotated[Annotated[str, Capacity(4)], Capacity(8)], "more than one Capacity"),
        (Final, "Final needs a type"),
        (list, "unsupported annotation"),
        ([int], "unsupported annotation"),
    ],
)
def test_annotations_that_cannot_be_stored_are_refused(
    hint: object, message: str
) -> None:
    """Check that an annotation the layout cannot store raises TypeError when the class is defined."""
    with pytest.raises(TypeError, match=message):
        build_layout(
            type("Bad", (), {"__annotations__": {"x": hint}, "__module__": "__main__"})
        )


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="typing.TypeAliasType is new in 3.12"
)
def test_type_aliases_are_unwrapped() -> None:
    """Check that a type alias stores as its value and hashes the same."""
    alias = typing.TypeAliasType("Celsius", float)  # type: ignore[attr-defined]
    layout = build_layout(
        type("T", (), {"__annotations__": {"x": alias}, "__module__": "__main__"})
    )
    assert layout.by_name["x"].kind == "float"
    assert (
        layout.schema_hash
        == build_layout(
            type("T", (), {"__annotations__": {"x": float}, "__module__": "__main__"})
        ).schema_hash
    )


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="typing.TypeAliasType is new in 3.12"
)
def test_an_alias_naming_something_undefined_is_a_type_error() -> None:
    """Check that an alias whose value names an undefined class raises TypeError, not NameError."""
    namespace: dict[str, object] = {}
    exec("type A = Undefined", namespace)
    with pytest.raises(TypeError, match="is not defined"):
        build_layout(
            type(
                "T",
                (),
                {"__annotations__": {"x": namespace["A"]}, "__module__": "__main__"},
            )
        )
