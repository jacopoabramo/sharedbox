import enum
from typing import Any, Literal

import pytest
from crossproc import snapshot_in_child, update_in_child

from sharedbox import SharedBox
from sharedbox._layout import build_layout


class Colour(enum.Enum):
    RED = "r"
    GREEN = "g"
    CRIMSON = "r"  # noqa: PIE796


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Other(enum.IntEnum):
    LOW = 1


class Perm(enum.Flag):
    R = 4
    W = 2
    X = 1


class Bits(enum.IntFlag):
    A = 1
    B = 1 << 63


class Choice(SharedBox):
    colour: Colour = Colour.RED
    level: Level = Level.LOW
    perm: Perm = Perm.R
    bits: Bits = Bits.A
    mode: Literal["fast", "slow", 1, True, None, Level.HIGH, b"raw"] = "fast"  # noqa: PYI061


def test_members_and_values_survive_another_process(unique_name: str) -> None:
    """Check that enum members, flag combinations and literal values read back as the same objects in a spawned process."""
    with Choice.create(unique_name) as box:
        box.update(
            colour=Colour.GREEN,
            perm=Perm.R | Perm.W,
            bits=Bits.A | Bits.B,
            mode=Level.HIGH,
        )
        seen = snapshot_in_child(box)
        assert seen["colour"] is Colour.GREEN
        assert seen["perm"] == Perm.R | Perm.W
        assert seen["bits"] == Bits.A | Bits.B
        assert seen["mode"] is Level.HIGH
        update_in_child(box, level=Level.HIGH, mode=True)
        assert box.level is Level.HIGH
        assert box.mode is True
    Choice.unlink(unique_name)


@pytest.mark.parametrize("value", ["fast", "slow", 1, True, None, Level.HIGH, b"raw"])
def test_each_literal_value_reads_back_with_its_type(
    unique_name: str, value: Any
) -> None:
    """Check that every literal value, 1 and True included, reads back equal and of the same type."""
    with Choice.create(unique_name) as box:
        box.mode = value
        assert box.mode == value
        assert type(box.mode) is type(value)
    Choice.unlink(unique_name)


def test_an_alias_reads_back_as_its_canonical_member(unique_name: str) -> None:
    """Check that an enum alias is stored as the member it names."""
    with Choice.create(unique_name) as box:
        box.colour = Colour.CRIMSON
        assert box.colour is Colour.RED
    Choice.unlink(unique_name)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("level", 1, TypeError),
        ("level", Other.LOW, TypeError),
        ("colour", "r", TypeError),
        ("perm", 4, TypeError),
        ("mode", "medium", ValueError),
        ("bits", Bits(1 << 64), ValueError),
    ],
)
def test_values_outside_the_type_are_refused(
    unique_name: str, field: str, value: object, error: type[Exception]
) -> None:
    """Check that an int, another enum's member or a value outside a Literal raises, leaving the field as it was."""
    with Choice.create(unique_name) as box:
        before = getattr(box, field)
        with pytest.raises(error, match=f"Choice.{field}"):
            setattr(box, field, value)
        assert getattr(box, field) == before
    Choice.unlink(unique_name)


def make(name: str, annotation: object) -> type:
    return type(
        name, (), {"__annotations__": {"x": annotation}, "__module__": "__main__"}
    )


def test_the_hash_follows_the_members_not_the_class_name() -> None:
    """Check that the schema hash changes with an enum's members and a literal's values, and not with the enum's name."""
    Same = enum.Enum("Same", ["RED", "GREEN", "CRIMSON"])
    More = enum.Enum("More", ["RED", "GREEN", "CRIMSON", "BLUE"])
    assert (
        build_layout(make("T", Colour)).schema_hash
        != build_layout(make("T", More)).schema_hash
    )
    assert (
        build_layout(make("T", Same)).schema_hash
        != build_layout(make("T", Colour)).schema_hash
    )
    Renamed = enum.Enum("Renamed", ["RED", "GREEN"])
    Two = enum.Enum("Two", ["RED", "GREEN"])
    assert (
        build_layout(make("T", Renamed)).schema_hash
        == build_layout(make("T", Two)).schema_hash
    )
    assert (
        build_layout(make("T", Literal["a", "b"])).schema_hash
        != build_layout(make("T", Literal["a", "c"])).schema_hash
    )


@pytest.mark.parametrize(
    "annotation",
    [
        enum.Flag("Wide", [f"F{i}" for i in range(65)]),
        Literal[1.5],
        Literal[2**63],
        enum.Enum("Empty", []),
    ],
)
def test_types_the_layout_cannot_keep_are_refused(annotation: object) -> None:
    """Check that a flag of 65 members, a float or out-of-range literal, and an enum with no members raise TypeError."""
    with pytest.raises(TypeError):
        build_layout(make("T", annotation))
