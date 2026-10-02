# ruff: noqa: RUF012, PYI025
import sys
import typing
from collections.abc import Iterator, Mapping, Sequence, Set
from typing import Annotated, Any

import pytest
from crossproc import snapshot_in_child, update_in_child

from sharedbox import Capacity, SharedBox
from sharedbox._layout import build_layout

Name = Annotated[str, Capacity(8)]


class Ragged(Mapping[str, int]):
    def __getitem__(self, key: str) -> int:
        return 0

    def __iter__(self) -> Iterator[str]:
        return iter(["a"])

    def __len__(self) -> int:
        return 1

    def items(self) -> Any:
        return [("a",)]


class Bag(SharedBox):
    numbers: Annotated[list[float], Capacity(16)] = []
    names: Annotated[tuple[Name, ...], Capacity(4)] = ()
    tags: Annotated[set[int], Capacity(8)] = set()
    frozen: Annotated[frozenset[Name], Capacity(4)] = frozenset()
    scores: Annotated[dict[Name, int], Capacity(4)] = {}
    grid: Annotated[list[Annotated[list[int], Capacity(3)]], Capacity(3)] = []
    seq: Annotated[Sequence[int], Capacity(4)] = []
    view: Annotated[Mapping[Name, float], Capacity(2)] = {}
    group: Annotated[Set[int], Capacity(2)] = set()
    raw: Annotated[list[Annotated[bytearray, Capacity(4)]], Capacity(2)] = []
    maybe: Annotated[list[int] | None, Capacity(3)] = None


VALUES = {
    "numbers": [0.5, -1.0, 2.0],
    "names": ("a", "bb"),
    "tags": {3, 1, 2},
    "frozen": frozenset({"x"}),
    "scores": {"one": 1, "two": 2},
    "grid": [[1, 2, 3], [], [4]],
    "seq": [9, 8],
    "view": {"k": 0.25},
    "group": {7},
    "raw": [bytearray(b"ab")],
    "maybe": [1, 2],
}


def test_collections_survive_another_process(unique_name: str) -> None:
    """Check that each collection reads back equal and of its declared type, abc forms as list, set and dict, in a spawned process."""
    with Bag.create(unique_name, **VALUES) as box:
        seen = snapshot_in_child(box)
        assert seen == VALUES
        assert [type(seen[k]) for k in VALUES] == [
            list,
            tuple,
            set,
            frozenset,
            dict,
            list,
            list,
            dict,
            set,
            list,
            list,
        ]
        assert type(seen["raw"][0]) is bytearray
        update_in_child(box, numbers=[1.0] * 16, scores={})
        assert box.numbers == [1.0] * 16
        assert box.scores == {}
    Bag.unlink(unique_name)


def test_a_read_collection_is_a_copy(unique_name: str) -> None:
    """Check that changing a list read from the box leaves the stored one as it was."""
    with Bag.create(unique_name, **VALUES) as box:
        numbers = box.numbers
        numbers.append(9.0)
        assert box.numbers == VALUES["numbers"]
    Bag.unlink(unique_name)


def test_a_read_copies_only_the_used_slots(unique_name: str) -> None:
    """Check that the raw bytes of a list hold its length and the used slots, not its whole capacity."""
    with Bag.create(unique_name, **VALUES) as box:
        index = Bag.__layout__.by_name["numbers"].index
        _, raw = box._segment.read_versioned(index)
        assert len(raw) == 8 + 3 * 8
    Bag.unlink(unique_name)


@pytest.mark.parametrize(
    ("field_name", "value", "error"),
    [
        ("numbers", [0.0] * 17, ValueError),
        ("numbers", "abc", TypeError),
        ("names", ("nine chars",), ValueError),
        ("tags", [1, 2], TypeError),
        ("scores", {"k": "v"}, TypeError),
        ("grid", [[1, 2, 3, 4]], ValueError),
        ("seq", b"ab", TypeError),
        ("scores", Ragged(), TypeError),
    ],
)
def test_collections_that_do_not_fit_are_refused(
    unique_name: str, field_name: str, value: object, error: type[Exception]
) -> None:
    """Check that too many elements, an oversized element, text for a sequence and the wrong container raise, leaving the field as it was."""
    with Bag.create(unique_name, **VALUES) as box:
        with pytest.raises(error, match=f"Bag.{field_name}"):
            setattr(box, field_name, value)
        assert getattr(box, field_name) == VALUES[field_name]
    Bag.unlink(unique_name)


def make(annotation: object) -> type:
    return type(
        "T", (), {"__annotations__": {"x": annotation}, "__module__": "__main__"}
    )


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (list[int], "Capacity"),
        (Annotated[list[str], Capacity(2)], "unsupported"),
        (Annotated[set[Annotated[list[int], Capacity(2)]], Capacity(2)], "hashable"),
        (
            Annotated[dict[Annotated[bytearray, Capacity(2)], int], Capacity(2)],
            "hashable",
        ),
        (Annotated[list, Capacity(2)], "Capacity does not apply"),
        (Annotated[typing.List, Capacity(2)], "unsupported"),  # noqa: UP006
        (Annotated[typing.Dict, Capacity(2)], "unsupported"),  # noqa: UP006
        (Annotated[list[int, str], Capacity(2)], "unsupported"),  # type: ignore[misc]
    ],
)
def test_collections_the_layout_cannot_keep_are_refused(
    annotation: object, message: str
) -> None:
    """Check that a collection without a capacity, an element without one, an unhashable set element or key, and a bare list raise TypeError."""
    with pytest.raises(TypeError, match=message):
        build_layout(make(annotation))


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="typing.TypeAliasType is new in 3.12"
)
def test_an_alias_that_contains_itself_is_refused() -> None:
    """Check that a recursive type alias raises TypeError rather than recursing without end."""
    tree = typing.TypeAliasType("Tree", Annotated[list["Tree"], Capacity(2)])  # type: ignore[attr-defined,name-defined]  # noqa: F821
    with pytest.raises(TypeError):
        build_layout(make(tree))


def test_an_optional_collection_holds_a_list_or_none(unique_name: str) -> None:
    """Check that a list or None in an optional field reads back as stored."""
    with Bag.create(unique_name, **VALUES) as box:
        assert box.maybe == [1, 2]
        box.maybe = None
        assert box.maybe is None
    Bag.unlink(unique_name)


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="the type statement is new in 3.12"
)
def test_an_alias_that_names_itself_is_refused() -> None:
    """Check that a type statement alias that refers to itself raises TypeError."""
    namespace = {"Annotated": Annotated, "Capacity": Capacity}
    exec("type Tree = Annotated[list[Tree], Capacity(2)]", namespace)  # noqa: S102
    with pytest.raises(TypeError, match="refers to itself"):
        build_layout(make(namespace["Tree"]))
