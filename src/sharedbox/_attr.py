"""Attr fields: a box field whose owner publishes a value that other processes read."""

from __future__ import annotations

import operator
import sys
import weakref
from collections.abc import Awaitable, Callable
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Generic,
    NoReturn,
    NotRequired,
    SupportsIndex,
    TypeVar,
    cast,
    get_origin,
    overload,
)

if sys.version_info >= (3, 13):
    from typing import ParamSpec, TypedDict
else:
    from typing_extensions import ParamSpec, TypedDict

from ._native import BoxClosedError
from ._request import RequestFuture, RequestServer

if TYPE_CHECKING:
    from ._box import SharedBox
    from ._layout import AttrInfo, FieldSpec

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
    """Present when read from a [`Setting`][sharedbox.Setting]."""
    timestamp_ns: int
    """The time of the last publish of the value, in nanoseconds since the Unix epoch."""


class Attr(Generic[T, P]):
    """A box field that its owner publishes and every process reads.

    Annotate a field `Attr[T]` and declare it with [`attr`][sharedbox.attr].
    `T` is the type of its value, or `None` for an attribute without one.
    The field holds no value that a process assigns: reading it from a box
    gives the `Attr` bound to that box, the same object every time, so an
    `Attr` is not built directly.

    An attribute stores the time of each publish of its value. It starts
    with its value at the zero of `T`, or at its `default`, and with a
    timestamp of 0. `T` may be anything a
    record member may be, except a [`Timed`][sharedbox.Timed], an `Attr` or a
    reference to a box.

    An attribute with argument types `P`, or with a `T` of `None`, takes
    requests: calling it sends one, and [`serve`][sharedbox.Attr.serve]
    answers them. A [`Setting`][sharedbox.Setting] also has a setpoint and
    takes its requests through `set`. Answering a request publishes nothing.
    """

    __slots__ = ("_box_ref", "_spec")

    def __init__(self, box: SharedBox, spec: FieldSpec) -> None:
        self._box_ref = weakref.ref(box)
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
    def timestamp_ns(self) -> int:
        """The time of the last publish of the value, in nanoseconds since the Unix epoch.

        Raises
        ------
        TypeError
            If the attribute has no value.
        BoxClosedError
            If the box is closed.
        """
        return self.read()["timestamp_ns"]

    def read(self) -> AttrValue[T]:
        """Return the value and its timestamp, with the setpoint of a `Setting`, from one write.

        Raises
        ------
        TypeError
            If the attribute has no value.
        BoxClosedError
            If the box is closed.
        """
        self._require("value")
        box = self._live()
        return cast(
            "AttrValue[T]",
            box._segment.get(self._spec.index, type(box).__layout__.types),
        )

    def publish(self, *, value: T = UNSET, timestamp_ns: SupportsIndex = UNSET) -> None:
        """Write the value, and its time if given, in one write.

        A value given without `timestamp_ns` is stamped with the time of the
        write, and so is one given `-2**63`.

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
        self._publish(value, UNSET, timestamp_ns)

    def _publish(self, value: Any, setpoint: Any, timestamp_ns: Any) -> None:
        """Write the parts that are not `UNSET` in one write."""
        box = self._live()
        parts = 0
        for given, part in (
            (value, PUBLISH_VALUE),
            (timestamp_ns, PUBLISH_TIMESTAMP),
            (setpoint, PUBLISH_SETPOINT),
        ):
            if given is not UNSET:
                parts |= part
        label = self._spec.label
        stamp = 0
        if not parts:
            raise TypeError(f"{label}: publish() needs value, setpoint or timestamp_ns")
        if parts & PUBLISH_VALUE:
            self._require("value")
        if parts & PUBLISH_TIMESTAMP:
            if not parts & PUBLISH_VALUE:
                raise TypeError(f"{label}: timestamp_ns needs a value to go with it")
            try:
                if isinstance(timestamp_ns, bool):
                    raise TypeError
                stamp = operator.index(timestamp_ns)
            except TypeError:
                raise TypeError(
                    f"{label}: timestamp_ns must be an int, not {type(timestamp_ns).__name__}"
                ) from None
            if not INT64_MIN <= stamp <= INT64_MAX:
                raise OverflowError(f"{label}: timestamp_ns does not fit 64 bits")
        box._segment.attr_publish(
            self._spec.index,
            parts,
            None if value is UNSET else value,
            None if setpoint is UNSET else setpoint,
            stamp,
            type(box).__layout__.types,
        )

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> RequestFuture[T]:
        """Send a request with `args` and return the future of its answer.

        The request is sent whether or not anyone waits for the future, and
        replaces an earlier one the server has not taken yet.

        Raises
        ------
        TypeError
            If the attribute takes no requests or is a `Setting`, an argument
            is given by keyword, or the arguments do not match its argument types.
        ValueError
            If an argument does not fit its type.
        OverflowError
            If a number is out of the range of its type.
        BoxClosedError
            If the box is closed.
        """
        info = self._info()
        if info.settable:
            raise TypeError(
                f"{self._spec.label} is a Setting; send its requests with set()"
            )
        if not info.request:
            raise TypeError(f"{self._spec.label} takes no requests")
        if kwargs:
            raise TypeError(f"{self._spec.label} takes positional arguments only")
        return self._send(args)

    @overload
    def serve(self, handler: Callable[P, Awaitable[T]]) -> RequestServer: ...
    @overload
    def serve(self, handler: Callable[P, T]) -> RequestServer: ...
    def serve(self, handler: Callable[..., Any]) -> RequestServer:
        """Answer the attribute's requests with `handler` until the server is stopped or the box closed.

        A sync handler runs on a thread of its own; an async one runs as a
        task on the event loop running at this call. The handler handles
        one request at a time, and of the requests sent meanwhile only the
        newest is handled next. Its return value is the answer; an
        exception it raises is logged to the `sharedbox` logger and reaches
        the caller as [`RequestError`][sharedbox.RequestError]. A
        [`Setting`][sharedbox.Setting]'s handler receives the value given to
        [`set`][sharedbox.Setting.set].

        Raises
        ------
        TypeError
            If the attribute takes no requests.
        AlreadyServedError
            If a live process serves the field already, this one included.
        RuntimeError
            If `handler` is async and no event loop is running.
        BoxClosedError
            If the box is closed.
        """
        if not self._info().request:
            raise TypeError(f"{self._spec.label} takes no requests")
        return self._live()._watcher.serve(self._spec, handler)

    def _send(self, args: tuple[Any, ...]) -> RequestFuture[T]:
        box = self._live()
        segment = box._segment
        index = self._spec.index
        since = segment.version(index)
        number = segment.request_send(index, args, type(box).__layout__.types)
        return box._watcher.call_future(self._spec, number, since)

    def _info(self) -> AttrInfo:
        """Return the parts of the attribute, or raise `BoxClosedError` once its box has been freed."""
        self._live()
        info = self._spec.attr
        assert info is not None
        return info

    def _live(self) -> SharedBox:
        """Return the box, or raise `BoxClosedError` once it has been freed."""
        box = self._box_ref()
        if box is None:
            raise BoxClosedError(f"{self._spec.label}: its box is gone")
        return box

    def _require(self, part: str) -> None:
        """Raise `TypeError` unless the attribute has `part`."""
        info = self._info()
        label = self._spec.label
        match part:
            case "value" if not info.has_value:
                raise TypeError(f"{label} has no value")


class Setting(Attr[T, []]):
    """An attribute with a setpoint, which other processes ask its owner to change.

    Annotate a field `Setting[T]` and declare it with
    [`attr`][sharedbox.attr]. A `Setting` is an [`Attr`][sharedbox.Attr]
    that also holds a setpoint of type `T`; its requests carry the new
    value. Reading the field from a box gives the `Setting` bound to that
    box, so a `Setting` is not built directly.
    """

    __slots__ = ()

    @property
    def setpoint(self) -> T:
        """The setpoint, as of the last publish.

        Raises
        ------
        BoxClosedError
            If the box is closed.
        """
        return self.read()["setpoint"]

    def publish(
        self,
        *,
        value: T = UNSET,
        setpoint: T = UNSET,
        timestamp_ns: SupportsIndex = UNSET,
    ) -> None:
        """Write the parts given in one write, so a reader never sees parts of different publishes together.

        A value given without `timestamp_ns` is stamped with the time of the
        write, and so is one given `-2**63`. A `setpoint` alone keeps the
        value and its time.

        Raises
        ------
        TypeError
            If nothing is given, `timestamp_ns` comes without `value`, or a
            part has the wrong type.
        ValueError
            If a part does not fit its type.
        OverflowError
            If `timestamp_ns` does not fit 64 bits.
        BoxClosedError
            If the box is closed.
        """
        self._publish(value, setpoint, timestamp_ns)

    def set(self, value: T) -> RequestFuture[T]:
        """Send `value` as the one argument of a request, and return the future of its answer.

        The handler given to [`serve`][sharedbox.Setting.serve] receives
        `value` and decides what to publish.

        Raises
        ------
        TypeError
            If `value` does not have the attribute's type.
        ValueError
            If `value` does not fit its type.
        BoxClosedError
            If the box is closed.
        """
        return self._send((value,))

    # The base takes a handler of no arguments, since a Setting is an Attr[T]
    # with no argument types; a Setting's request carries the new value.
    @overload  # type: ignore[override]
    def serve(self, handler: Callable[[T], Awaitable[T]]) -> RequestServer: ...
    @overload
    def serve(self, handler: Callable[[T], T]) -> RequestServer: ...
    def serve(self, handler: Callable[..., Any]) -> RequestServer:
        """Answer the setting's requests with `handler` until the server is stopped or the box closed.

        `handler` receives the value given to [`set`][sharedbox.Setting.set]
        and is served as [`Attr.serve`][sharedbox.Attr.serve] describes.

        Raises
        ------
        AlreadyServedError
            If a live process serves the field already, this one included.
        RuntimeError
            If `handler` is async and no event loop is running.
        BoxClosedError
            If the box is closed.
        """
        return super().serve(handler)


def is_attr_annotation(hint: object) -> bool:
    """Return whether `hint` is `Attr`, `Setting` or a subscription of either."""
    origin = get_origin(hint) or hint
    return isinstance(origin, type) and issubclass(origin, Attr)


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
            info = self.spec.attr
            kind = Setting if info is not None and info.settable else Attr
            handle = handles.setdefault(self.spec.index, kind(box, self.spec))
        return handle

    def refuse(self) -> NoReturn:
        raise TypeError(
            f"{self.spec.label} is an Attr field; publish to it instead of assigning it"
        )

    def __set__(self, box: object, value: object) -> NoReturn:
        self.refuse()

    def __delete__(self, box: object) -> NoReturn:
        self.refuse()
