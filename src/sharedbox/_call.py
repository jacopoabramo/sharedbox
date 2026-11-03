"""Call fields: a box field through which any process asks the process that serves it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, NoReturn, ParamSpec, TypeVar

if TYPE_CHECKING:
    from ._box import SharedBox
    from ._layout import FieldSpec

P = ParamSpec("P")
R = TypeVar("R")


class Call(Generic[P, R]):
    """A box field through which a process sends a typed request to the process that serves it.

    Annotate a field `Call[[A, ...], R]`, where the list holds the argument
    types and `R` the result type, or `None` for no result. The field holds
    no value: reading it from a box gives the `Call` bound to that box.
    Arguments and result may be anything a record member may be, except a
    [`Timed`][sharedbox.Timed], a `Call` or a reference to a box.
    """

    __slots__ = ("_box", "_spec")

    def __init__(self, box: SharedBox, spec: FieldSpec) -> None:
        self._box = box
        self._spec = spec


class CallAttribute:
    """The descriptor of a call field: reading gives its [`Call`][sharedbox.Call], assigning or deleting raises."""

    __slots__ = ("spec",)

    def __init__(self, spec: FieldSpec) -> None:
        self.spec = spec

    def __get__(
        self, box: SharedBox | None, owner: type | None = None
    ) -> Call[..., Any] | CallAttribute:
        if box is None:
            return self
        return Call(box, self.spec)

    def refuse(self) -> NoReturn:
        raise TypeError(
            f"{self.spec.label} is a Call field; call it instead of assigning it"
        )

    def __set__(self, box: object, value: object) -> NoReturn:
        self.refuse()

    def __delete__(self, box: object) -> NoReturn:
        self.refuse()
