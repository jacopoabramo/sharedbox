"""Release the native objects of boxes, stream ends and box classes when the interpreter exits."""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
from typing import Any, Final, NoReturn, cast

from ._box import LIVE, SharedBox
from ._events import stop_watchers
from ._native import BoxClosedError, BoxMethod, StreamClosedError
from ._native import Field as FieldDescriptor
from ._refs import CLASSES
from ._stream import (
    LIVE_ENDS,
    SharedReceiveStream,
    SharedSendStream,
    stop_stream_workers,
)

RELEASED: Final = (
    "is unavailable: sharedbox released its native objects at interpreter exit"
)


class ReleasedAttribute:
    """Takes the place of a native field or method of a box class, or of its layout, once released."""

    def __init__(self, label: str) -> None:
        self.label = label

    def refuse(self) -> NoReturn:
        raise BoxClosedError(f"{self.label} {RELEASED}")

    def __get__(self, box: object, owner: type | None = None) -> NoReturn:
        self.refuse()

    def __set__(self, box: object, value: object) -> NoReturn:
        self.refuse()


class ReleasedNative:
    """Takes the place of the native objects of a box, a stream or a stream end once released.

    It reads as closed and closing it does nothing; any other attribute
    raises `error`.
    """

    closed = True

    def __init__(self, name: str, error: type[Exception]) -> None:
        self.name = name
        self.error = error

    def close(self) -> None:
        pass

    def __getattr__(self, attr: str) -> NoReturn:
        raise self.error(f"{self.name!r} {RELEASED}")


def release_box(box: SharedBox) -> None:
    """Close `box` and replace its segment, value cache and watcher."""
    if isinstance(box._segment, ReleasedNative):
        return
    # Not waiting: a watcher thread still in a callback after stop_watchers gave up on it would hang the exit.
    with contextlib.suppress(Exception):
        box._close(False)
    released = cast(Any, ReleasedNative(box._segment.name, BoxClosedError))
    box._segment = released
    box._values = released
    box._watcher = released
    box._refs = {}


def release_end(end: SharedSendStream[Any] | SharedReceiveStream[Any]) -> None:
    """Close `end` and its stream, and replace their native objects and the stream's item types."""
    stream = end._stream
    for closing in (end, stream):
        with contextlib.suppress(Exception):
            closing.close()
    released = cast(Any, ReleasedNative(stream.name, StreamClosedError))
    end._native = released
    stream._native = released
    stream._item = dataclasses.replace(stream._item, types=released)


def release_class(cls: type[SharedBox]) -> None:
    """Replace the native fields and methods of `cls`, and its layout, which holds the field types."""
    for attr, value in list(cls.__dict__.items()):
        if isinstance(value, (FieldDescriptor, BoxMethod)):
            setattr(cls, attr, ReleasedAttribute(f"{cls.__qualname__}.{attr}"))
    cls.__layout__ = cast(Any, ReleasedAttribute(cls.__qualname__))


def release_native() -> None:
    """Close every box and stream end and release the native objects of every box class.

    Registered with `atexit`. It stops the watcher and stream worker
    threads first, and then releases, so that no native object outlives
    the interpreter however long the classes and boxes are kept alive.
    Afterwards, reading or
    writing a field, or creating or attaching a box, raises
    [`BoxClosedError`][sharedbox.BoxClosedError]; `close` does nothing.
    Calling it again does nothing more.
    """
    stop_watchers()
    stop_stream_workers()
    for box in list(LIVE):
        release_box(box)
    for end in list(LIVE_ENDS):
        release_end(end)
    for refs in list(CLASSES.values()):
        for ref in tuple(refs):
            cls = ref()
            if cls is not None:
                release_class(cls)


atexit.register(release_native)
