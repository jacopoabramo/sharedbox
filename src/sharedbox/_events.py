from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from ._native import BoxClosedError

if TYPE_CHECKING:
    from ._layout import FieldSpec
    from ._native import Segment

T = TypeVar("T")
PENDING, DONE, CANCELLED = "pending", "done", "cancelled"
POLL = 0.1
logger = logging.getLogger("sharedbox")


class FieldFuture(Generic[T]):
    """The next value written to one field of a box.

    Resolves on the first write to the field after the version it was
    created at. Done callbacks run on the box's watcher thread.
    """

    __slots__ = ("_callbacks", "_cond", "_field", "_since", "_state", "_value", "_version", "_watcher")

    def __init__(self, watcher: Watcher, field: FieldSpec, since: int) -> None:
        self._watcher = watcher
        self._field = field
        self._since = since
        self._cond = threading.Condition()
        self._state = PENDING
        self._value: T | None = None
        self._version = since
        self._callbacks: list[Callable[[FieldFuture[T]], object]] = []

    @property
    def version(self) -> int:
        """Version of the field when the result was read."""
        return self._version

    def done(self) -> bool:
        return self._state != PENDING

    def cancelled(self) -> bool:
        return self._state == CANCELLED

    def result(self, timeout: float | None = None) -> T:
        """Block until the field changes and return its new value."""
        with self._cond:
            if not self._cond.wait_for(self.done, timeout):
                raise TimeoutError(f"{self._field.name} did not change within {timeout} s")
        if self._state == CANCELLED:
            raise CancelledError()
        # DONE is only set by _settle together with the value, so the cast holds.
        return cast(T, self._value)

    def cancel(self) -> bool:
        """Stop waiting. Returns False if the future already has a value."""
        if self._settle(CANCELLED, None, self._since):
            self._watcher.discard(self)
            return True
        return self.cancelled()

    def add_done_callback(self, fn: Callable[[FieldFuture[T]], object]) -> None:
        """Call ``fn(future)`` once it is done, at once if it already is."""
        with self._cond:
            if self._state == PENDING:
                self._callbacks.append(fn)
                return
        self._run(fn)

    def _settle(self, state: str, value: T | None, version: int) -> bool:
        with self._cond:
            if self._state != PENDING:
                return False
            self._state, self._value, self._version = state, value, version
            callbacks, self._callbacks = self._callbacks, []
            self._cond.notify_all()
        for fn in callbacks:
            self._run(fn)
        return True

    def _run(self, fn: Callable[[FieldFuture[T]], object]) -> None:
        try:
            fn(self)
        except Exception:
            logger.exception("callback for field %r raised", self._field.name)


class FieldWatch(Generic[T]):
    """Values written to one field after the watch was created.

    A consumer slower than the writers gets the latest value and skips the
    ones in between. Iteration ends when the box is closed.
    """

    __slots__ = ("_field", "_since", "_watcher")

    def __init__(self, watcher: Watcher, field: FieldSpec, since: int) -> None:
        self._watcher = watcher
        self._field = field
        self._since = since

    def __iter__(self) -> Iterator[T]:
        since = self._since
        while True:
            try:
                fut: FieldFuture[T] = self._watcher.future(self._field, since)
            except BoxClosedError:
                return
            try:
                value = fut.result()
            except CancelledError:
                if self._watcher.stopped:
                    return
                raise
            since = fut.version
            yield value


class Watcher:
    """Resolves the pending futures of one box from a background thread."""

    __slots__ = ("_lock", "_pending", "_segment", "_stop", "_thread")

    def __init__(self, segment: Segment) -> None:
        self._segment = segment
        self._pending: list[FieldFuture[Any]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def future(self, field: FieldSpec, since: int | None = None) -> FieldFuture[Any]:
        """A future for the first write to ``field`` after version ``since`` (default: now)."""
        current = self._segment.version(field.index)
        fut: FieldFuture[Any] = FieldFuture(self, field, current if since is None else since)
        if current != fut._since:
            fut._settle(DONE, field.decode(self._segment.read(field.index)), current)
            return fut
        with self._lock:
            if self._stop.is_set():
                fut._settle(CANCELLED, None, fut._since)
                return fut
            self._pending.append(fut)
            self._start_locked()
        return fut

    def discard(self, fut: FieldFuture[Any]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._pending.remove(fut)

    def stop(self) -> None:
        """Stop the thread and cancel every pending future."""
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self._lock:
            pending, self._pending = self._pending, []
        for fut in pending:
            fut._settle(CANCELLED, None, fut._since)

    def _start_locked(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name=f"sharedbox-watch-{self._segment.name}", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        with contextlib.suppress(BoxClosedError):
            generation = self._segment.generation()
            while not self._stop.is_set():
                self._resolve_ready()
                if self._stop.is_set():
                    return
                generation = self._segment.wait(generation, POLL)

    def _resolve_ready(self) -> None:
        with self._lock:
            pending = list(self._pending)
        ready = [(fut, self._segment.version(fut._field.index)) for fut in pending]
        ready = [(fut, version) for fut, version in ready if version != fut._since]
        if not ready:
            return
        with self._lock:
            for fut, _ in ready:
                with contextlib.suppress(ValueError):
                    self._pending.remove(fut)
        try:
            for fut, version in ready:
                fut._settle(DONE, fut._field.decode(self._segment.read(fut._field.index)), version)
        finally:
            for fut, _ in ready:
                fut._settle(CANCELLED, None, fut._since)
