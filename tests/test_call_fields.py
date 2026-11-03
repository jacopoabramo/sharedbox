from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import numpy as np
import pytest

from sharedbox import Call, Capacity, SharedBox, SharedStream, Timed, field, fields


class Scanner(SharedBox):
    position: float = 0.0
    move: Call[[float], float] = field(init=False)
    home: Call[[], None] = field(init=False)


class Bare(SharedBox):
    move: Call[[float], float]


class Plain(SharedBox):
    count: int = 0


@dataclass
class Holder:
    call: Call[[int], int]


def test_a_box_with_call_fields_is_created_without_values_for_them(
    names: Callable[[str], str],
) -> None:
    """Create a box with call fields from the values of its other fields alone."""
    with (
        Scanner.create(names("none")) as empty,
        Scanner.create(names("one"), 1.5) as box,
    ):
        assert empty.position == 0.0
        assert box.snapshot() == {"position": 1.5}
        assert next(f for f in fields(Scanner) if f.name == "move").init is False
        assert "move" not in repr(box)
        assert "home" not in repr(box)


def test_a_call_field_refuses_value_operations(names: Callable[[str], str]) -> None:
    """Raise TypeError for each way of treating a call field as a value."""
    with Scanner.create(names("refuse")) as box:
        with pytest.raises(TypeError):
            box.move = 1  # type: ignore[assignment]
        with pytest.raises(TypeError):
            del box.move
        with pytest.raises(TypeError):
            box.update(move=1)
        with pytest.raises(TypeError):
            box.watch("move")
        with pytest.raises(TypeError):
            box.events.move  # noqa: B018
        with pytest.raises(TypeError):
            box.read_into("move", np.zeros(1))
        with pytest.raises(TypeError), box.writing("move"):
            pass
        box.position = 2.5
        assert box.position == 2.5


def test_call_is_refused_where_it_cannot_go() -> None:
    """Raise TypeError for a call inside another type, an untyped call, and arguments that cannot be stored."""
    with pytest.raises(TypeError):

        class InList(SharedBox):
            calls: Annotated[list[Call[[int], int]], Capacity(2)]

    with pytest.raises(TypeError):

        class Optional(SharedBox):
            maybe: Call[[int], int] | None

    with pytest.raises(TypeError):

        class InRecord(SharedBox):
            holder: Holder

    with pytest.raises(TypeError):

        class InTimed(SharedBox):
            stamped: Timed[Call[[int], int]]

    with pytest.raises(TypeError, match="list of argument types"):

        class Untyped(SharedBox):
            move: Call[..., int] = field(init=False)

    with pytest.raises(TypeError):

        class TimedArgument(SharedBox):
            move: Call[[Timed[int]], int] = field(init=False)

    with pytest.raises(TypeError):

        class TimedResult(SharedBox):
            move: Call[[int], Timed[int]] = field(init=False)

    with pytest.raises(TypeError):

        class ReferenceArgument(SharedBox):
            move: Call[[Plain], int] = field(init=False)

    with pytest.raises(TypeError):
        SharedStream.create(Call[[int], int], capacity=2)


def test_a_bare_call_annotation_also_works(names: Callable[[str], str]) -> None:
    """Create a box whose call field has no field() and no default."""
    with Bare.create(names("bare")) as box:
        assert box.snapshot() == {}
