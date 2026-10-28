"""Release the native objects of boxes, streams, watchers and box classes when the interpreter exits."""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
from typing import Any, Final, NoReturn, cast

from ._bounded import _cached_codec
from ._box import LIVE, SharedBox
from ._events import WATCHERS, Watcher, stop_watchers
from ._native import (
    BoxClosedError,
    BoxMethod,
    Segment,
    Stream,
    StreamClosedError,
    Types,
    ValueCache,
)
from ._native import Field as FieldDescriptor
from ._refs import CLASSES
from ._stream import (
    LIVE_ENDS,
    LIVE_STREAMS,
    SharedReceiveStream,
    SharedSendStream,
    SharedStream,
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
    """Takes the place of a native object of a box or a stream once released.

    It reads as closed and closing it does nothing. Any other attribute of
    the class `native` raises `error`; an attribute `native` lacks raises
    `AttributeError`, as the object it replaces would.
    """

    closed = True

    def __init__(self, name: str, error: type[Exception], native: type) -> None:
        self.name = name
        self.error = error
        self.native = native

    def close(self) -> None:
        pass

    def __getattr__(self, attr: str) -> NoReturn:
        if hasattr(self.native, attr):
            raise self.error(f"{self.name!r} {RELEASED}")
        raise AttributeError(
            f"{self.native.__name__!r} object has no attribute {attr!r}"
        )


def release_box(box: SharedBox) -> None:
    """Close `box` and replace its segment and value cache."""
    if isinstance(box._segment, ReleasedNative):
        return
    # Not waiting: a watcher thread still in a callback after stop_watchers gave up on it would hang the exit.
    with contextlib.suppress(Exception):
        box._close(False)
    name = box._segment.name
    box._segment = cast(Any, ReleasedNative(name, BoxClosedError, Segment))
    box._values = cast(Any, ReleasedNative(name, BoxClosedError, ValueCache))
    box._refs = {}


def release_watcher(watcher: Watcher) -> None:
    """Replace the segment and field types of `watcher`, which the watches and events groups of its box reach."""
    if isinstance(watcher._segment, ReleasedNative):
        return
    name = watcher._segment.name
    watcher._segment = cast(Any, ReleasedNative(name, BoxClosedError, Segment))
    watcher._types = cast(Any, ReleasedNative(name, BoxClosedError, Types))
    watcher._seen = {}
    if watcher.follower is not None:
        watcher.follower.outer = watcher._segment


def release_end(end: SharedSendStream[Any] | SharedReceiveStream[Any]) -> None:
    """Close `end` and replace its native end."""
    if isinstance(end._native, ReleasedNative):
        return
    with contextlib.suppress(Exception):
        end.close()
    end._native = cast(
        Any, ReleasedNative(end._stream.name, StreamClosedError, type(end._native))
    )


def release_stream(stream: SharedStream[Any]) -> None:
    """Close `stream` and replace its native stream and its item types."""
    if isinstance(stream._native, ReleasedNative):
        return
    with contextlib.suppress(Exception):
        stream.close()
    stream._native = cast(Any, ReleasedNative(stream.name, StreamClosedError, Stream))
    stream._item = dataclasses.replace(
        stream._item,
        types=cast(Any, ReleasedNative(stream.name, StreamClosedError, Types)),
    )


def release_class(cls: type[SharedBox]) -> None:
    """Replace the native fields and methods of `cls`, and its layout, which holds the field types."""
    for attr, value in list(cls.__dict__.items()):
        if isinstance(value, (FieldDescriptor, BoxMethod)):
            setattr(cls, attr, ReleasedAttribute(f"{cls.__qualname__}.{attr}"))
    cls.__layout__ = cast(Any, ReleasedAttribute(cls.__qualname__))


def release_native() -> None:
    """Close every box, stream and stream end, and release the native objects of watchers and box classes.

    Registered with `atexit`. It stops the watcher and stream worker
    threads first, and then releases, so that no native object outlives
    the interpreter however long the classes, boxes, streams, watches and
    events groups are kept alive. It also empties the cache of
    bounded-collection codecs. Afterwards, reading or writing a field, or
    creating or attaching a box, raises
    [`BoxClosedError`][sharedbox.BoxClosedError], and using a stream or
    one of its ends raises
    [`StreamClosedError`][sharedbox.StreamClosedError]; `close` does
    nothing. Calling it again does nothing more.
    """
    stop_watchers()
    stop_stream_workers()
    for box in list(LIVE):
        release_box(box)
    for watcher in list(WATCHERS):
        release_watcher(watcher)
    for end in list(LIVE_ENDS):
        release_end(end)
    for stream in list(LIVE_STREAMS):
        release_stream(stream)
    for refs in list(CLASSES.values()):
        for ref in tuple(refs):
            cls = ref()
            if cls is not None:
                release_class(cls)
    _cached_codec.cache_clear()


atexit.register(release_native)
