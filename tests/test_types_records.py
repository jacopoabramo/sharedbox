import collections
import dataclasses
import datetime
import subprocess
import sys
from dataclasses import InitVar, dataclass
from typing import Annotated, Generic, NamedTuple, NotRequired, TypedDict, TypeVar

import pytest
from crossproc import snapshot_in_child, update_in_child

from sharedbox import Capacity, SharedBox, field
from sharedbox._layout import build_layout

T = TypeVar("T")


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass
class Sample:
    label: Annotated[str, Capacity(8)]
    point: Point
    seen: datetime.datetime | None = None
    built = 0

    def __post_init__(self) -> None:
        type(self).built += 1


class Pair(NamedTuple):
    a: int
    b: Annotated[bytes, Capacity(4)]


class Options(TypedDict):
    speed: float
    name: NotRequired[Annotated[str, Capacity(8)]]


class Shapes(SharedBox):
    sample: Sample = Sample("s", Point(0.0, 0.0))
    pair: Pair = Pair(0, b"")
    coords: tuple[int, float, bool] = (0, 0.0, False)
    options: Options = field(default_factory=lambda: Options(speed=1.0))


VALUES = {
    "sample": Sample("label", Point(1.5, -2.0), datetime.datetime(2026, 1, 1)),  # noqa: DTZ001
    "pair": Pair(3, b"ab"),
    "coords": (7, 0.25, True),
    "options": Options(speed=2.5, name="fast"),
}


def test_records_survive_another_process(unique_name: str) -> None:
    """Check that each record kind reads back equal, as its declared class, in a spawned process and after its update."""
    with Shapes.create(unique_name, **VALUES) as box:
        seen = snapshot_in_child(box)
        assert seen == VALUES
        assert type(seen["sample"]) is Sample
        assert type(seen["sample"].point) is Point
        assert type(seen["pair"]) is Pair
        assert type(seen["coords"]) is tuple
        assert type(seen["options"]) is dict
        update_in_child(box, pair=Pair(9, b"cd"), options=Options(speed=0.5))
        assert box.pair == Pair(9, b"cd")
        assert box.options == {"speed": 0.5}
    Shapes.unlink(unique_name)


def test_a_read_record_is_a_copy(unique_name: str) -> None:
    """Check that changing a record read from the box leaves the stored one as it was."""
    with Shapes.create(unique_name, **VALUES) as box:
        sample = box.sample
        sample.label = "changed"
        assert box.sample.label == "label"
    Shapes.unlink(unique_name)


def test_reading_a_record_calls_its_class(unique_name: str) -> None:
    """Check that each read builds the record through its constructor, so __post_init__ runs."""
    with Shapes.create(unique_name, **VALUES) as box:
        before = Sample.built
        box.sample  # noqa: B018
        box.sample  # noqa: B018
        assert Sample.built == before + 2
    Shapes.unlink(unique_name)


@pytest.mark.parametrize(
    ("field_name", "value", "error"),
    [
        ("sample", {"label": "x"}, TypeError),
        ("pair", (1, b"a"), TypeError),
        ("coords", (1, 2.0), ValueError),
        ("options", {"speed": 1.0, "extra": 1}, TypeError),
        ("options", {"name": "x"}, TypeError),
        ("sample", Sample("nine char", Point(0, 0)), ValueError),
    ],
)
def test_record_values_that_do_not_fit_are_refused(
    unique_name: str, field_name: str, value: object, error: type[Exception]
) -> None:
    """Check that the wrong class, a tuple of the wrong length, a TypedDict with an extra or missing key and an oversized member raise."""
    with Shapes.create(unique_name, **VALUES) as box:
        with pytest.raises(error, match=f"Shapes.{field_name}"):
            setattr(box, field_name, value)
        assert getattr(box, field_name) == VALUES[field_name]
    Shapes.unlink(unique_name)


def nested(depth: int) -> type:
    """A dataclass holding a dataclass, `depth` records deep."""
    cls: type = dataclasses.make_dataclass("Leaf", [("value", int)])
    for level in range(depth - 1):
        cls = dataclasses.make_dataclass(f"Level{level}", [("inner", cls)])
    return cls


def make(annotation: object) -> type:
    return type(
        "T", (), {"__annotations__": {"x": annotation}, "__module__": "__main__"}
    )


def test_records_nest_16_deep() -> None:
    """Check that records nest 16 levels deep and a 17th is refused."""
    build_layout(make(nested(16)))
    with pytest.raises(TypeError, match="16 levels"):
        build_layout(make(nested(17)))


@dataclass
class Hidden:
    a: int
    b: int = dataclasses.field(default=0, init=False)


@dataclass
class Init:
    a: int
    scale: InitVar[int]


@dataclass(init=False)
class NoInit:
    a: int


Untyped = collections.namedtuple("Untyped", ["a"])


@dataclass
class Generic1(Generic[T]):
    a: int


class Motor(SharedBox):
    position: int = 0


@dataclass
class Holding:
    motor: Motor


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (Hidden, "init=False"),
        (Init, "InitVar"),
        (NoInit, "init=False"),
        (Untyped, "no type"),
        (Generic1, "generic"),
        (Holding, "reference"),
        (tuple[()], "unsupported"),
    ],
)
def test_records_that_cannot_be_rebuilt_are_refused(
    annotation: object, message: str
) -> None:
    """Check that records whose constructor does not take every stored member, generic records and references inside records raise TypeError."""
    with pytest.raises(TypeError, match=message):
        build_layout(make(annotation))


def test_the_hash_follows_the_structure_not_the_class_name() -> None:
    """Check that two record classes with the same members hash the same and a changed member changes the hash."""
    first = dataclasses.make_dataclass("First", [("a", int), ("b", float)])
    second = dataclasses.make_dataclass("Second", [("a", int), ("b", float)])
    changed = dataclasses.make_dataclass("First", [("a", int), ("b", int)])
    assert (
        build_layout(make(first)).schema_hash == build_layout(make(second)).schema_hash
    )
    assert (
        build_layout(make(first)).schema_hash != build_layout(make(changed)).schema_hash
    )


@dataclass
class Base:
    scale: InitVar[int] = 1


@dataclass
class Derived(Base):
    a: int = 0


def test_an_initvar_with_a_default_on_a_base_class_is_accepted() -> None:
    """Check that an InitVar whose default is declared on a base dataclass is not refused."""
    build_layout(make(Derived))


def test_a_not_required_key_counts_toward_the_depth_limit() -> None:
    """Check that a record under a NotRequired key at the 16th level is refused by Python, not the native parser."""
    outer = TypedDict("outer", {"k": NotRequired[nested(15)]})  # type: ignore[misc]
    with pytest.raises(TypeError, match="16 levels"):
        build_layout(make(outer))


class Noted(TypedDict):
    note: NotRequired[Annotated[str, Capacity(8)] | None]


class Notes(SharedBox):
    noted: Noted = field(default_factory=lambda: Noted())


def test_a_missing_key_differs_from_a_none_value(unique_name: str) -> None:
    """Check that a NotRequired key holding None reads back as None and an absent key stays absent."""
    with Notes.create(unique_name) as box:
        box.noted = {"note": None}
        assert box.noted == {"note": None}
        box.noted = {}
        assert box.noted == {}
    Notes.unlink(unique_name)


def test_an_absent_key_is_not_added_by_a_default_factory(unique_name: str) -> None:
    """Check that writing a defaultdict leaves its missing NotRequired key out."""
    with Notes.create(unique_name) as box:
        source: collections.defaultdict[str, str] = collections.defaultdict(str)
        box.noted = source  # type: ignore[assignment]
        assert box.noted == {}
        assert not source
    Notes.unlink(unique_name)


RECORD_CLASS_AT_EXIT = """
import dataclasses

from sharedbox import SharedBox


@dataclasses.dataclass
class Inner:
    x: int


class Outer(SharedBox):
    inner: Inner
"""


def test_a_record_field_class_is_freed_at_exit() -> None:
    """Check that a box class whose record type refers back to its module leaves no leak report at exit."""
    done = subprocess.run(
        [sys.executable, "-c", RECORD_CLASS_AT_EXIT],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    )
    assert "leaked" not in done.stderr
