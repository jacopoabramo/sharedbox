import datetime
import decimal
import enum
import struct
import sys
from typing import Annotated, Literal

import pytest
from crossproc import snapshot_in_child

from sharedbox import Capacity, SharedBox
from sharedbox._layout import build_layout


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Pick(SharedBox):
    num: float | int = 0
    flag: int | bool = 0
    maybe: Annotated[str | None, Capacity(8)] = None
    when: datetime.date | datetime.datetime = datetime.date(2026, 1, 1)
    level: int | Level = 0
    many: int | Annotated[str, Capacity(4)] | None = None
    money: Annotated[decimal.Decimal | None, Capacity(8)] = None
    either: int | Annotated[decimal.Decimal, Capacity(8)] = 0
    wide: int | float = 0


class Stamp(datetime.datetime):
    pass


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("num", 3),
        ("num", 3.5),
        ("flag", True),
        ("flag", 7),
        ("maybe", None),
        ("maybe", "eight ch"),
        ("when", datetime.datetime(2026, 1, 1, 12)),  # noqa: DTZ001
        ("when", datetime.date(2026, 1, 2)),
        ("level", Level.HIGH),
        ("level", 2),
        ("many", None),
        ("many", 5),
        ("many", "abcd"),
    ],
)
def test_each_member_reads_back_with_its_own_type(
    unique_name: str, field: str, value: object
) -> None:
    """Check that a union stores the member the value's type picks, so 3 stays an int and True a bool, across processes."""
    with Pick.create(unique_name) as box:
        setattr(box, field, value)
        seen = snapshot_in_child(box)[field]
        assert seen == value
        assert type(seen) is type(value)
    Pick.unlink(unique_name)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("num", "3", TypeError),
        ("maybe", "nine char", ValueError),
        ("many", "abcde", ValueError),
        ("flag", None, TypeError),
    ],
)
def test_a_value_no_member_takes_is_refused(
    unique_name: str, field: str, value: object, error: type[Exception]
) -> None:
    """Check that a value of no member's type raises TypeError, and one whose member refuses its contents raises that member's error."""
    with Pick.create(unique_name) as box:  # noqa: SIM117
        with pytest.raises(error, match=f"Pick.{field}"):
            setattr(box, field, value)
    Pick.unlink(unique_name)


def test_a_stored_tag_past_the_members_raises(unique_name: str) -> None:
    """Check that a union tag no member has, written raw, raises ValueError on read."""
    with Pick.create(unique_name) as box:
        index = Pick.__layout__.by_name["num"].index
        box._segment._write([(index, struct.pack("<B", 9) + bytes(15))])
        with pytest.raises(ValueError, match="Pick.num"):
            box.num  # noqa: B018
    Pick.unlink(unique_name)


def make(annotation: object) -> type:
    return type(
        "T", (), {"__annotations__": {"x": annotation}, "__module__": "__main__"}
    )


class Motor(SharedBox):
    position: int = 0


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (Literal["a"] | Literal["b"], "same class"),  # noqa: PYI030
        (Annotated[str | bytes, Capacity(4)], "exactly one member"),
        (int | Motor, "reference"),
        (Annotated[int | None, Capacity(4)], "Capacity does not apply"),
    ],
)
def test_unions_the_layout_cannot_tell_apart_are_refused(
    annotation: object, message: str
) -> None:
    """Check that ambiguous unions, a misplaced Capacity and a reference inside a union raise TypeError."""
    with pytest.raises(TypeError, match=message):
        build_layout(make(annotation))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("money", decimal.Decimal("1.50")),
        ("money", None),
        ("either", decimal.Decimal("1.50")),
        ("either", 5),
    ],
)
def test_a_decimal_member_round_trips(
    unique_name: str, field: str, value: object
) -> None:
    """Check that a Decimal inside an optional or a union reads back with its text and type."""
    with Pick.create(unique_name) as box:
        setattr(box, field, value)
        seen = snapshot_in_child(box)[field]
        assert str(seen) == str(value)
        assert type(seen) is type(value)
    Pick.unlink(unique_name)


def test_a_subclass_value_takes_the_most_specific_member(unique_name: str) -> None:
    """Check that a datetime subclass in a date | datetime union keeps its time."""
    with Pick.create(unique_name) as box:
        box.when = Stamp(2026, 1, 1, 12, 30)
        assert box.when == datetime.datetime(2026, 1, 1, 12, 30)  # noqa: DTZ001
        assert type(box.when) is datetime.datetime
    Pick.unlink(unique_name)


def test_a_member_that_fails_is_not_followed_by_the_next(unique_name: str) -> None:
    """Check that an int too large for the int member raises OverflowError and leaves the field alone."""
    with Pick.create(unique_name) as box:
        box.wide = 7
        with pytest.raises(OverflowError):
            box.wide = 2**70
        assert box.wide == 7
    Pick.unlink(unique_name)


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="typing.TypeAliasType is new in 3.12"
)
def test_mutually_recursive_aliases_are_a_type_error() -> None:
    """Check that two aliases naming each other raise TypeError, not RecursionError."""
    namespace: dict[str, object] = {}
    exec("type A = int | B; type B = str | A", namespace)  # noqa: S102
    with pytest.raises(TypeError, match="refers to itself"):
        build_layout(make(namespace["A"]))
