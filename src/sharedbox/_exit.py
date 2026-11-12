"""Release the native objects of boxes, streams, watchers and box classes when the interpreter exits."""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
import logging
import threading
from collections.abc import Callable
from typing import Any, Final, NoReturn, TypeVar, cast

from ._bounded import _cached_codec
from ._box import LIVE, SharedBox
from ._events import WATCHERS, Watcher, stop_watchers
from ._native import (
    BoxClosedError,
    BoxMethod,
    LockTimeoutError,
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

T = TypeVar("T")
logger = logging.getLogger("sharedbox")
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


def release_each(release: Callable[[T], object], targets: list[T]) -> None:
    """Run `release` on each of `targets`; one that raises is logged and does not keep the others from being released."""
    for target in targets:
        try:
            release(target)
        except Exception:
            # Not the repr: a box's repr reads it, which may fail again once its release has.
            shown = target if isinstance(target, type) else type(target)
            logger.warning(
                "%s of %s at exit failed",
                release.__name__,
                shown.__qualname__,
                exc_info=True,
            )


def quiet_hook(
    previous: Callable[[threading.ExceptHookArgs], Any], state: dict[str, bool]
) -> Callable[[threading.ExceptHookArgs], Any]:
    """Return a `threading.excepthook` that passes on to `previous`, except the errors of a closed box or stream once `state` says the interpreter exits."""

    # Everything the hook reads is bound here, since it may run after module globals are cleared.
    def hook(
        args: threading.ExceptHookArgs,
        # A wait for the lock that the release cuts short reports a lock timeout.
        closed: tuple[type[Exception], ...] = (
            BoxClosedError,
            StreamClosedError,
            LockTimeoutError,
        ),
        is_instance: Callable[[object, Any], bool] = isinstance,
    ) -> None:
        if state["exiting"] and is_instance(args.exc_value, closed):
            return
        previous(args)

    return hook


# The release at exit wakes every thread that waits inside a box or a stream with BoxClosedError,
# StreamClosedError or, from a wait for the lock, LockTimeoutError. A daemon thread that does not catch it prints a traceback while the interpreter
# finalizes, and an interpreter that cannot take the lock of stderr from such a thread aborts the
# process. A thread falls back on the hook that was installed when it was created, because the
# module's own is gone by then, so the hook goes in at import and stays silent until the exit.
EXIT_STATE: Final[dict[str, bool]] = {"exiting": False}
threading.excepthook = quiet_hook(threading.excepthook, EXIT_STATE)


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
    nothing. Calling it again does nothing more. A thread that ends on
    one of those two errors from then on, or on the
    [`LockTimeoutError`][sharedbox.LockTimeoutError] of a lock wait the
    release cut short, prints no traceback.
    """
    if not EXIT_STATE["exiting"]:
        EXIT_STATE["exiting"] = True
        # Again, around a hook installed since the import, for the threads still running now.
        threading.excepthook = quiet_hook(threading.excepthook, EXIT_STATE)
    for stop in (stop_watchers, stop_stream_workers):
        try:
            stop()
        except Exception:
            logger.warning("%s at exit failed", stop.__name__, exc_info=True)
    # Classes first, so a late thread that reads a field meets BoxClosedError rather than a replaced segment.
    release_each(
        release_class,
        [
            cls
            for refs in list(CLASSES.values())
            for ref in tuple(refs)
            if (cls := ref()) is not None
        ],
    )
    release_each(release_box, list(LIVE))
    release_each(release_watcher, list(WATCHERS))
    release_each(release_end, list(LIVE_ENDS))
    release_each(release_stream, list(LIVE_STREAMS))
    _cached_codec.cache_clear()


atexit.register(release_native)
