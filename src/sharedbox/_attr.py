"""Attr fields: a box field whose owner publishes a value that other processes read."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Final, Generic, NoReturn, NotRequired, TypeVar, cast

if sys.version_info >= (3, 13):
    from typing import ParamSpec, TypedDict
else:
    from typing_extensions import ParamSpec, TypedDict

if TYPE_CHECKING:
    from ._box import SharedBox
    from ._layout import FieldSpec

T = TypeVar("T")
P = ParamSpec("P", default=[])
PUBLISH_VALUE: Final = 1
PUBLISH_TIMESTAMP: Final = 2
PUBLISH_SETPOINT: Final = 4
INT64_MIN: Final = -(2**63)
INT64_MAX: Final = 2**63 - 1


class Unset:
    """Stands for a `publish` argument that was not given."""

    def __repr__(self) -> str:
        return "<unset>"


UNSET: Any = Unset()


class AttrValue(TypedDict, Generic[T]):
    """What [`Attr.read`][sharedbox.Attr.read] returns: the parts of an attribute, read together."""

    value: T
    setpoint: NotRequired[T]
    """Present when the attribute is settable."""
    timestamp_ns: NotRequired[int]
    """Present when the attribute is timed."""


class Attr(Generic[T, P]):
    """A box field that its owner publishes and every process reads.

    Annotate a field `Attr[T]` and declare it with [`attr`][sharedbox.attr].
    `T` is the type of its value, or `None` for an attribute without one.
    The field holds no value that a process assigns: reading it from a box
    gives the `Attr` bound to that box, the same object every time.

    An attribute starts with its value and setpoint at the zero of `T`, or
    at its `default`, and with a timestamp of 0. `T` may be anything a
    record member may be, except a [`Timed`][sharedbox.Timed], an `Attr` or a
    reference to a box.
    """

    __slots__ = ("_box", "_spec")

    def __init__(self, box: SharedBox, spec: FieldSpec) -> None:
        self._box = box
        self._spec = spec

    @property
    def value(self) -> T:
        """The value, as of the last publish.

        Raises
        ------
        TypeError
            If the attribute has no value.
        BoxClosedError
            If the box is closed.
        """
        return self.read()["value"]

    @property
    def setpoint(self) -> T:
        """The setpoint, as of the last publish.

        Raises
        ------
        TypeError
            If the attribute is not settable.
        BoxClosedError
            If the box is closed.
        """
        self._require("setpoint")
        return self.read()["setpoint"]

    @property
    def timestamp_ns(self) -> int:
        """The time of the last publish of the value, in nanoseconds since the Unix epoch.

        Raises
        ------
        TypeError
            If the attribute is not timed.
        BoxClosedError
            If the box is closed.
        """
        self._require("timestamp_ns")
        return self.read()["timestamp_ns"]

    def read(self) -> AttrValue[T]:
        """Return the value, with the setpoint and the timestamp where the attribute has them, from one write.

        Raises
        ------
        TypeError
            If the attribute has no value.
        BoxClosedError
            If the box is closed.
        """
        self._require("value")
        return cast(
            "AttrValue[T]",
            self._box._segment.get(
                self._spec.index, type(self._box).__layout__.types
            ),
        )

    def publish(
        self,
        *,
        value: T = UNSET,
        setpoint: T = UNSET,
        timestamp_ns: int = UNSET,
    ) -> None:
        """Write the parts given in one write, so a reader never sees parts of different publishes together.

        A value given without `timestamp_ns` on a timed attribute is stamped
        with the time of the write, and so is one given `-2**63`. A
        `setpoint` alone keeps the value and its time.

        Raises
        ------
        TypeError
            If nothing is given, `timestamp_ns` comes without `value`, a part
            is not one the attribute has, or a part has the wrong type.
        ValueError
            If a part does not fit its type.
        OverflowError
            If `timestamp_ns` does not fit 64 bits.
        BoxClosedError
            If the box is closed.
        """
        parts = 0
        for given, part in (
            (value, PUBLISH_VALUE),
            (timestamp_ns, PUBLISH_TIMESTAMP),
            (setpoint, PUBLISH_SETPOINT),
        ):
            if given is not UNSET:
                parts |= part
        label = self._spec.label
        if not parts:
            raise TypeError(
                f"{label}: publish() needs value, setpoint or timestamp_ns"
            )
        if parts & PUBLISH_VALUE:
            self._require("value")
        if parts & PUBLISH_SETPOINT:
            self._require("setpoint")
        if parts & PUBLISH_TIMESTAMP:
            self._require("timestamp_ns")
            if not parts & PUBLISH_VALUE:
                raise TypeError(f"{label}: timestamp_ns needs a value to go with it")
            if not isinstance(timestamp_ns, int) or isinstance(timestamp_ns, bool):
                raise TypeError(
                    f"{label}: timestamp_ns must be an int, not {type(timestamp_ns).__name__}"
                )
            if not INT64_MIN <= timestamp_ns <= INT64_MAX:
                raise OverflowError(f"{label}: timestamp_ns does not fit 64 bits")
        self._box._segment.attr_publish(
            self._spec.index,
            parts,
            None if value is UNSET else value,
            None if setpoint is UNSET else setpoint,
            0 if timestamp_ns is UNSET else timestamp_ns,
            type(self._box).__layout__.types,
        )

    def _require(self, part: str) -> None:
        """Raise `TypeError` unless the attribute has `part`."""
        info = self._spec.attr
        assert info is not None
        label = self._spec.label
        match part:
            case "value" if not info.has_value:
                raise TypeError(f"{label} has no value")
            case "setpoint" if not info.settable:
                raise TypeError(f"{label} is not settable, so it has no setpoint")
            case "timestamp_ns" if not info.timed:
                raise TypeError(f"{label} is not timed, so it has no timestamp_ns")


class AttrAttribute:
    """The descriptor of an attr field: reading gives its [`Attr`][sharedbox.Attr], assigning or deleting raises."""

    __slots__ = ("spec",)

    def __init__(self, spec: FieldSpec) -> None:
        self.spec = spec

    def __get__(
        self, box: SharedBox | None, owner: type | None = None
    ) -> Attr[Any, ...] | AttrAttribute:
        if box is None:
            return self
        handles = box._attrs
        handle = handles.get(self.spec.index)
        if handle is None:
            handle = handles.setdefault(self.spec.index, Attr(box, self.spec))
        return handle

    def refuse(self) -> NoReturn:
        raise TypeError(
            f"{self.spec.label} is an Attr field; publish to it instead of assigning it"
        )

    def __set__(self, box: object, value: object) -> NoReturn:
        self.refuse()

    def __delete__(self, box: object) -> NoReturn:
        self.refuse()
