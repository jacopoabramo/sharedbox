from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import threading
import time
import weakref
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from concurrent.futures import CancelledError, Future, InvalidStateError
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Generic,
    NoReturn,
    TypeAlias,
    TypeVar,
    overload,
)

from psygnal import Signal, SignalGroup

from ._native import BoxClosedError, LockTimeoutError, WaiterSlotsFullError
from ._refs import shown
from ._request import (
    LIVENESS_DELAY,
    AlreadyServedError,
    Outcome,
    RequestFuture,
    RequestServer,
)

if TYPE_CHECKING:
    from psygnal import SignalInstance

    from ._follow import Follower
    from ._layout import FieldSpec, Layout
    from ._native import Segment, Types

T = TypeVar("T")
STEP: Final = 1.0
# Seconds the exit handler waits for stopped watcher threads, together.
EXIT_WAIT: Final = 2.0
logger = logging.getLogger("sharedbox")
# Receives each change of a field as native values: the field, the new value, the old one.
Sink: TypeAlias = "Callable[[FieldSpec, Any, Any], None]"


class WatcherThread(threading.local):
    """Marks watcher threads: one must not join another, since that one may be joining it."""

    active = False


WATCHER_THREAD = WatcherThread()
RUNNING: set[Watcher] = set()
# Every watcher, for the exit handler: a watch or an events group can keep one after its box is gone.
WATCHERS: weakref.WeakSet[Watcher] = weakref.WeakSet()


def on_watcher_thread() -> bool:
    """Return whether the calling thread is the watcher thread of some box."""
    return WATCHER_THREAD.active


def stop_watchers() -> None:
    """Stop every watcher thread and wait for them to end, before the interpreter finalizes.

    It stops the servers of attr fields too, and waits for their threads.
    The wait lasts at most `EXIT_WAIT` seconds in all; a thread still running
    a callback or a handler after that is logged and left running. On CPython before 3.14, a daemon thread that takes the GIL back during
    finalization is ended with `pthread_exit` on Linux. The unwinding that
    starts there aborts the process when it reaches the native call that
    released the GIL, such as a watcher's wait or a box's `close`.
    """
    # A copy, because watcher threads remove themselves from the set while this runs.
    watchers = RUNNING.copy()
    threads = [thread for watcher in watchers for thread in watcher.threads()]
    for watcher in watchers:
        watcher.stop(wait=False)
    # Bounded, so a callback that never returns cannot keep the process from exiting.
    deadline = time.monotonic() + EXIT_WAIT
    for thread in threads:
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
    late = [
        thread.name
        for thread in threads
        if thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ]
    if late:
        logger.warning(
            "watcher threads still running at exit after %s s: %s",
            EXIT_WAIT,
            ", ".join(late),
        )


class FieldFuture(Future[T]):
    """The next value written to one field of a box.

    A `concurrent.futures.Future`, so `concurrent.futures.wait`,
    `concurrent.futures.as_completed` and `asyncio.wrap_future` take it, and
    it can be awaited directly in a running event loop. It resolves on the
    first write to the field after the version it was created at; for an
    attr field, on the first publish, so a request leaves it pending. Done
    callbacks run on the box's watcher thread, or on the thread that closes
    the box; closing the box cancels the futures still pending.

    [`FieldWatch.future`][sharedbox.FieldWatch.future] returns one.
    """

    def __init__(
        self,
        watcher: Watcher,
        field: FieldSpec,
        since: int,
        published: int | None = None,
    ) -> None:
        super().__init__()
        self._watcher = watcher
        self._field = field
        self._since = since
        self._version = since
        # An attr field's publish count to compare with; None for every other kind.
        self._published = published
        self._notified = False

    @property
    def version(self) -> int:
        """Version of the field when the result was read; before that, the version the future was created at."""
        return self._version

    def cancel(self) -> bool:
        """Stop waiting; False if the future already has a value, True otherwise."""
        if not super().cancel():
            return False
        # Marks the cancellation as reported, which concurrent.futures.wait needs
        # to count the future as done; a second call there would log an error.
        with self._condition:
            first = not self._notified
            self._notified = True
            if first:
                self.set_running_or_notify_cancel()
        self._watcher.discard(self)
        return True

    def result(self, timeout: float | None = None) -> T:
        """Block until the field changes and return its new value.

        Raises
        ------
        TimeoutError
            If the field did not change within `timeout` seconds.
        concurrent.futures.CancelledError
            If the future was cancelled.
        RuntimeError
            If the field has not changed and this is called from a callback
            of the same box's events: that thread is the one that resolves
            the future.
        """
        if not self.done() and self._watcher._thread is threading.current_thread():
            raise RuntimeError(
                f"{self._field.name}: result() would wait on the thread that resolves "
                "the future; use add_done_callback, or wait on another thread"
            )
        try:
            return super().result(timeout)
        except TimeoutError:
            raise TimeoutError(
                f"{self._field.name} did not change within {timeout} s"
            ) from None

    def __await__(self) -> Generator[Any, None, T]:
        return asyncio.wrap_future(self).__await__()

    def _resolve(self, value: T, version: int, published: int | None = None) -> None:
        if self.done():
            return
        self._version = version
        self._published = published
        with contextlib.suppress(InvalidStateError):
            self.set_result(value)

    def _fail(self, error: Exception) -> None:
        with contextlib.suppress(InvalidStateError):
            self.set_exception(error)


class FieldWatch(Generic[T]):
    """New values of one field, for `for`, `async for` or `future()`.

    [`SharedBox.watch`][sharedbox.SharedBox.watch] returns it. Only writes
    made after the watch was created count. Iterating with `for` blocks
    until the field is written and yields the new value; `async for` waits
    the same way without blocking the event loop. A consumer slower than
    the writers gets the latest value and skips the ones in between.
    Iteration ends when the box is closed.
    """

    __slots__ = ("_field", "_published", "_since", "_watcher")

    def __init__(
        self,
        watcher: Watcher,
        field: FieldSpec,
        since: int,
        published: int | None = None,
    ) -> None:
        self._watcher = watcher
        self._field = field
        self._since = since
        self._published = published

    def __iter__(self) -> Iterator[T]:
        since, published = self._since, self._published
        while True:
            try:
                fut: FieldFuture[T] = self._watcher.future(
                    self._field, since, published
                )
            except BoxClosedError:
                return
            try:
                value = fut.result()
            except CancelledError:
                if self._watcher.stopped:
                    return
                raise
            since, published = fut.version, fut._published
            yield value

    def __aiter__(self) -> AsyncIterator[T]:
        return self._values()

    def future(self) -> FieldFuture[T]:
        """Return a future of the next write to the field after the watch was created.

        A write made between [`watch`][sharedbox.SharedBox.watch] and this
        call resolves the future at once. Each call returns a new future
        from the same starting version, so one watch can hand out several.
        The future works with `concurrent.futures.wait`; see
        [`FieldFuture`][sharedbox.FieldFuture].

        Raises
        ------
        BoxClosedError
            If the box is closed.
        """
        return self._watcher.future(self._field, self._since, self._published)

    async def _values(self) -> AsyncIterator[T]:
        since, published = self._since, self._published
        while True:
            try:
                fut: FieldFuture[T] = self._watcher.future(
                    self._field, since, published
                )
            except BoxClosedError:
                return
            try:
                value = await fut
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if self._watcher.stopped and (task is None or not task.cancelling()):
                    return
                raise
            since, published = fut.version, fut._published
            yield value


class Watcher:
    """Resolves the pending futures of one box from a background thread."""

    __slots__ = (
        "__weakref__",
        "_calls",
        "_fields",
        "_group",
        "_lock",
        "_metadata_head",
        "_pending",
        "_seen",
        "_segment",
        "_servers",
        "_sink",
        "_slot",
        "_slots_full",
        "_stop",
        "_thread",
        "_types",
        "follower",
    )

    def __init__(self, segment: Segment, types: Types) -> None:
        self._segment = segment
        self._types = types
        self._pending: list[FieldFuture[Any]] = []
        self._calls: list[RequestFuture[Any]] = []
        # Each server, with the write count of its field when it was last kicked.
        self._servers: dict[RequestServer, int] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._group: BoxEvents | None = None
        self._sink: Sink | None = None
        # Moves forwarding when a reference field changes; set by the box's events group.
        self.follower: Follower | None = None
        self._fields: tuple[FieldSpec, ...] = ()
        self._seen: dict[int, tuple[int, bytes, Any]] = {}
        self._metadata_head = 0
        self._slot: int | None = None
        self._slots_full = False
        WATCHERS.add(self)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def future(
        self,
        field: FieldSpec,
        since: int | None = None,
        published: int | None = None,
    ) -> FieldFuture[Any]:
        """Return a future for the first write to `field` after version `since` (default: now).

        For an attr field it is the first publish after publish count
        `published`, which comes with `since` (default: now).
        """
        fut: FieldFuture[Any]
        if field.attr is None:
            current, value = self._segment.get_versioned(field.index, self._types)
            fut = FieldFuture(self, field, current if since is None else since)
            if current != fut._since:
                fut._resolve(shown(field, value), current)
                return fut
        else:
            # The write count first, so a publish between the two reads counts as made before the future.
            current = self._segment.version(field.index)
            now = self._segment.attr_published(field.index)
            if published is None:
                published = now
            fut = FieldFuture(self, field, current, published)
            if now != published:
                fut._resolve(*self._read_attr(field))
                return fut
        with self._lock:
            if self._stop.is_set():
                fut.cancel()
                return fut
            self._pending.append(fut)
            self._start_locked()
        return fut

    def call_future(
        self, spec: FieldSpec, number: int, since: int
    ) -> RequestFuture[Any]:
        """Return the future of the answer to request `number`, sent when the field's write count was `since`."""
        fut: RequestFuture[Any] = RequestFuture(self, spec, number, since)
        with self._lock:
            if self._stop.is_set():
                fut.cancel()
                return fut
            self._calls.append(fut)
            first = len(self._calls) == 1
            self._start_locked()
        slot = self._slot
        if first and slot is not None:
            # Ends a wait of STEP, so the liveness checks start at once.
            with contextlib.suppress(BoxClosedError):
                self._segment.interrupt(slot)
        return fut

    def serve(self, spec: FieldSpec, handler: Callable[..., Any]) -> RequestServer:
        """Claim attr field `spec` for this process and answer its requests with `handler`."""
        loop = None
        if inspect.iscoroutinefunction(handler):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                raise RuntimeError(
                    "serve() needs a running event loop for an async handler"
                ) from None
        with self._lock:
            ended = [
                server
                for server in self._servers
                if server._field.index == spec.index and server._loop_closed()
            ]
        for server in ended:
            server._halt(join=False)
        if not self._segment.serve_claim(spec.index):
            raise AlreadyServedError(f"{spec.label} is served already")
        server = RequestServer(self, spec, handler, loop)
        with self._lock:
            if self._stop.is_set():
                server._halt(join=False)
                raise BoxClosedError(f"box {self._segment.name!r} is closed")
            self._servers[server] = self._segment.version(spec.index)
            self._start_locked()
        server._start()
        return server

    @staticmethod
    def on_watcher_thread() -> bool:
        """Return whether the calling thread is the watcher thread of some box."""
        return on_watcher_thread()

    def forget(self, server: RequestServer) -> None:
        with self._lock:
            self._servers.pop(server, None)

    def threads(self) -> list[threading.Thread]:
        """Return the watcher thread and the threads of sync servers, those that were started."""
        with self._lock:
            threads = [server._thread for server in self._servers]
            threads.append(self._thread)
        return [t for t in threads if t is not None and t.ident is not None]

    def events(
        self, factory: Callable[[], BoxEvents], fields: tuple[FieldSpec, ...]
    ) -> BoxEvents:
        """Return the box's signal group, created on first use; the watcher emits into it while running."""
        with self._lock:
            if self._group is None:
                group = factory()
                self._metadata_head = self._segment.metadata_head()
                self._listen_locked(self._to_group, fields)
                # Stored last: a read above that raised must leave nothing behind, or the next
                # access would return a group the watcher never emits into.
                self._group = group
            self._start_locked()
            return self._group

    def listen(self, sink: Sink, fields: tuple[FieldSpec, ...]) -> None:
        """Pass every later change of `fields` to `sink`, on the watcher thread."""
        with self._lock:
            if self._sink is None:
                self._listen_locked(sink, fields)
            self._start_locked()

    def resume(self) -> None:
        """Start the thread again in a child created by `fork`, if changes have a sink."""
        with self._lock:
            if self._sink is not None:
                self._start_locked()

    def last(self, spec: FieldSpec) -> Any:
        """Return the value of `spec` the watcher last saw, which it compares the next change with."""
        return self._seen[spec.index][2]

    def _read_attr(self, spec: FieldSpec) -> tuple[Any, int, int]:
        """Return the value of attr field `spec`, its write count and its publish count, from one read."""
        version, raw = self._segment.read_versioned(spec.index)
        # The value part starts with the publish count.
        published = int.from_bytes(raw[:8], "little")
        return self._types.decode(spec.index, raw), version, published

    def _read(self, spec: FieldSpec) -> tuple[int, bytes, Any]:
        """Return the field's write count, stored bytes and value, from one read."""
        version, raw = self._segment.read_versioned(spec.index)
        return version, raw, self._types.decode(spec.index, raw)

    def _listen_locked(self, sink: Sink, fields: tuple[FieldSpec, ...]) -> None:
        self._seen = {spec.index: self._read(spec) for spec in fields}
        self._fields = fields
        self._sink = sink

    def _to_group(self, spec: FieldSpec, new: Any, old: Any) -> None:
        group = self._group
        assert group is not None
        try:
            group[spec.name].emit(shown(spec, new), shown(spec, old))
        except BaseException:
            # SystemExit included: it would end this thread and with it every later delivery.
            logger.exception("a callback for field %r raised", spec.name)
        # After the emission, so the reference signal's callbacks run before forwarding moves.
        if spec.target is not None and self.follower is not None:
            self.follower.moved(spec, new, self._segment)

    def _emit_metadata(self, group: BoxEvents | None) -> None:
        if group is None:
            return
        head = self._segment.metadata_head()
        if head != self._metadata_head:
            self._metadata_head = head
            try:
                group.metadata.emit(head)
            except Exception:
                logger.exception("a callback for the metadata raised")

    def _emit_changes(self) -> None:
        with self._lock:
            sink, fields, group = self._sink, self._fields, self._group
        if sink is None:
            return
        self._emit_metadata(group)
        versions = self._segment.versions()
        for spec in fields:
            seen_version, seen_raw, old = self._seen[spec.index]
            if versions[spec.index] == seen_version:
                continue
            version, raw = self._segment.read_versioned(spec.index)
            # Bytes, not ==: == raises for arrays and some decimals, and calls 1 and True the same.
            if raw == seen_raw:
                self._seen[spec.index] = (version, raw, old)
                continue
            try:
                new = self._types.decode(spec.index, raw)
            except Exception:
                self._seen[spec.index] = (version, raw, old)
                logger.exception("reading a change of field %r failed", spec.name)
                continue
            self._seen[spec.index] = (version, raw, new)
            # An update that wrote this field with a version signals the version first.
            self._emit_metadata(group)
            try:
                sink(spec, new, old)
            except Exception:
                logger.exception("forwarding a change of field %r failed", spec.name)

    def discard(self, fut: FieldFuture[Any] | RequestFuture[Any]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            if isinstance(fut, RequestFuture):
                self._calls.remove(fut)
            else:
                self._pending.remove(fut)

    def stop(self, wait: bool = True) -> None:
        """Stop the thread and every server, and cancel every pending future.

        Parameters
        ----------
        wait
            If true, also wait for the thread and the threads of sync
            servers to end, and deliver the writes the watcher had not
            seen yet.
        """
        self._stop.set()
        slot = self._slot
        if slot is not None:
            # Ends the watcher's wait now rather than at its next step.
            with contextlib.suppress(BoxClosedError):
                self._segment.interrupt(slot)
        with self._lock:
            servers = list(self._servers)
        for server in servers:
            # A field that cannot be released must not keep the futures below from being cancelled.
            try:
                server._halt(join=False)
            except LockTimeoutError as error:
                logger.warning("%s; %s stays served", error, server._field.label)
            except Exception:
                logger.exception(
                    "stopping the server of %s failed", server._field.label
                )
        with self._lock:
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join()
            try:
                self._resolve_ready()
                self._emit_changes()
            except LockTimeoutError as error:
                logger.warning("%s", error)
            except Exception:
                logger.exception(
                    "checking box %r for changes on close failed", self._segment.name
                )
        with self._lock:
            pending, self._pending = self._pending, []
            calls, self._calls = self._calls, []
        for fut in [*pending, *calls]:
            fut.cancel()
        # Last, so a handler waiting on a future of this box sees it cancelled and returns.
        if wait:
            for server in servers:
                server._join()

    def after_fork(self) -> None:
        """Forget the parent's thread, futures and servers in a child created by `fork`.

        The thread starts again on the next
        [`future`][sharedbox._events.Watcher.future] or
        [`events`][sharedbox._events.Watcher.events] call, or on
        [`resume`][sharedbox._events.Watcher.resume].
        """
        stopped = self._stop.is_set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        if stopped:
            self._stop.set()
        self._thread = None
        self._pending = []
        self._calls = []
        for server in self._servers:
            server._after_fork()
        self._servers = {}
        self._slot = None
        RUNNING.discard(self)

    def _start_locked(self) -> None:
        if self._thread is None and not self._stop.is_set():
            self._thread = threading.Thread(
                target=self._run,
                name=f"sharedbox-watch-{self._segment.name}",
                daemon=True,
            )
            RUNNING.add(self)
            self._thread.start()

    def _claim(self) -> int | None:
        """Return a waiter slot that still records this process, or None while every slot is taken."""
        slot = self._slot
        if slot is not None and self._segment.waiter_held(slot):
            return slot
        if slot is not None:
            # Freed under this watcher, by mistake or by a bookkeeping bug; claim another.
            self._segment.release_waiter(slot)
        try:
            self._slot = self._segment.register_waiter()
        except WaiterSlotsFullError as error:
            self._slot = None
            if not self._slots_full:
                logger.warning("%s; checking for changes once a second", error)
            self._slots_full = True
            return None
        self._slots_full = False
        return self._slot

    def _run(self) -> None:
        WATCHER_THREAD.active = True
        with contextlib.suppress(BoxClosedError):
            try:
                generation = self._segment.generation()
                timed_out = False
                check_at = 0.0
                calls = False
                while not self._stop.is_set():
                    try:
                        self._resolve_ready()
                        now = time.monotonic()
                        check = now >= check_at
                        if check:
                            check_at = now + LIVENESS_DELAY
                        calls = self._resolve_calls(check)
                        self._kick_servers()
                        self._emit_changes()
                        timed_out = False
                    except LockTimeoutError as error:
                        # A dead writer keeps the lock until force_unlock(); warn once per outage.
                        if not timed_out:
                            logger.warning("%s", error)
                        timed_out = True
                    except BoxClosedError:
                        raise
                    except BaseException:
                        # Anything else would end the thread, and the box would deliver nothing more.
                        logger.exception(
                            "checking box %r for changes failed", self._segment.name
                        )
                    slot = self._claim()
                    if self._stop.is_set():
                        return
                    step = LIVENESS_DELAY if calls else STEP
                    if slot is None:
                        self._stop.wait(step)
                        continue
                    # The step bounds how long a freed slot or a missed wake-up goes unnoticed.
                    generation = self._segment.wait(generation, step, slot)
            finally:
                RUNNING.discard(self)
                if self._slot is not None:
                    self._segment.release_waiter(self._slot)

    def _resolve_ready(self) -> None:
        with self._lock:
            pending = list(self._pending)
        if not pending:
            return
        versions = self._segment.versions()
        ready: list[FieldFuture[Any]] = []
        for fut in pending:
            index = fut._field.index
            if versions[index] == fut._since:
                continue
            if (
                fut._published is None
                or self._segment.attr_published(index) != fut._published
            ):
                ready.append(fut)
            else:
                # A request moved the write count; skip the future until the next write.
                fut._since = versions[index]
        if not ready:
            return
        # Read every value before removing any future, so a lock timeout leaves them all pending.
        values: list[tuple[FieldFuture[Any], tuple[Any, int, int | None]]] = []
        failed: list[tuple[FieldFuture[Any], Exception]] = []
        for fut in ready:
            try:
                if fut._published is None:
                    version, value = self._segment.get_versioned(
                        fut._field.index, self._types
                    )
                    values.append((fut, (shown(fut._field, value), version, None)))
                else:
                    values.append((fut, self._read_attr(fut._field)))
            except (BoxClosedError, LockTimeoutError):
                raise
            except Exception as error:
                # Decoding the value can run Python code, such as a record's __post_init__.
                failed.append((fut, error))
        with self._lock:
            for fut in ready:
                with contextlib.suppress(ValueError):
                    self._pending.remove(fut)
        for fut, (value, version, published) in values:
            fut._resolve(value, version, published)
        for fut, reason in failed:
            fut._fail(reason)

    def _resolve_calls(self, check: bool) -> bool:
        """Complete the request futures whose answer arrived; with `check`, read every one and check its server.

        Returns whether any is still pending.
        """
        with self._lock:
            calls = list(self._calls)
        if not calls:
            return False
        versions = self._segment.versions()
        outcomes: list[tuple[RequestFuture[Any], int | None, Any]] = []
        for fut in calls:
            version = versions[fut._field.index]
            if version == fut._since and not check:
                continue
            outcome: int | None
            try:
                outcome, value = self._segment.request_answer_of(
                    fut._field.index, fut._number, check, self._types
                )
            except (BoxClosedError, LockTimeoutError):
                raise
            except Exception as error:
                # Decoding the result can run Python code, such as a record's __post_init__.
                outcome, value = None, error
            if outcome == Outcome.PENDING:
                fut._since = version
            else:
                outcomes.append((fut, outcome, value))
        with self._lock:
            for fut, _, _ in outcomes:
                with contextlib.suppress(ValueError):
                    self._calls.remove(fut)
            pending = bool(self._calls)
        for fut, outcome, value in outcomes:
            if outcome is None:
                fut._fail(value)
            else:
                fut._complete(outcome, value)
        return pending

    def _kick_servers(self) -> None:
        with self._lock:
            servers = list(self._servers.items())
        if not servers:
            return
        versions = self._segment.versions()
        for server, seen in servers:
            # An async server whose loop ended while idle was never cancelled; free its field.
            if server._loop_closed():
                server._halt(join=False)
                continue
            version = versions[server._field.index]
            if version == seen:
                continue
            with self._lock:
                if server in self._servers:
                    self._servers[server] = version
            server._kick()


class BoxEvents(SignalGroup):
    """The psygnal signal group of a box: one `(new, old)` signal per field, and `metadata`.

    It is the type of [`SharedBox.events`][sharedbox.SharedBox.events] and
    of the groups [`follow`][sharedbox.BoxEvents.follow] returns. The
    group of a class with reference fields also has the signal
    [`nested`][sharedbox.BoxEvents.nested].
    """

    _sharedbox_follower: Follower
    metadata: SignalInstance
    """Emitted with the new version number when any process writes a new metadata version.

    Several versions between two checks of the watcher thread emit once, with
    the newest version. `box.events.connect(cb)` receives it too. A group
    that [`follow`][sharedbox.BoxEvents.follow] returns has it but does
    not emit it.
    """
    nested: SignalInstance
    """Emitted as `(path, new, old)` for each change inside the boxes that `follow()` without a field follows.

    `path` is the tuple of field names from this group's box to the
    changed field. It reports every field of each box's own class, and
    not changes of this box's own fields, which keep their own signals.
    `box.events.connect(cb)` receives it too. Only the group of a class
    with reference fields has it, and it emits only after
    [`follow`][sharedbox.BoxEvents.follow] is called without a field.
    """

    @overload
    def follow(self, field: str) -> BoxEvents: ...
    @overload
    def follow(self, field: None = None) -> None: ...
    def follow(self, field: str | None = None) -> BoxEvents | None:
        """Forward changes made inside the boxes that reference fields refer to.

        With `field`, return a group with the signals of the class `field`
        is annotated with, emitted for whichever box the field refers to
        when the change happens; a later call returns the same group.
        Without `field`, emit every change inside every box the reference
        fields reach, down the whole graph, on
        [`nested`][sharedbox.BoxEvents.nested].

        The group `follow(field)` returns has `follow` and `unfollow` too,
        so `office.events.follow("carrier").follow("bag")` reaches one
        level further. Its signals are those of the annotated class: a
        field that only a subclass has is left out.

        Forwarding follows each box once. A box it already follows, this
        group's box included, is not followed again where another
        reference leads to it, so a loop of references ends. A box that
        several paths reach is reported under one of them, and after a
        reference changes that may be a different one; it is forwarded as
        long as any path reaches it. An empty reference forwards nothing
        until a box is assigned.

        When a reference changes, in any process, the watcher emits the
        reference's own signal first, then moves forwarding to the new
        box. Values the new box already held are not emitted, and a change
        made to the old box just before the move may not be. Moving
        forwarding away from a box waits for that box's callbacks to
        return.

        A reference to a box that was removed or created again, or whose
        class this process has not defined, logs a warning to the
        `sharedbox` logger and forwards nothing until the field is assigned
        another box; `follow` does not raise for it. Any other error from
        attaching a box is raised by the `follow` call that attached it;
        that call then follows nothing new, and the next call tries again.
        When a reference change starts the attach instead, the error is
        logged, and that box is tried again the next time any reference
        field followed through this group changes.

        Forwarding attaches its own handle to each box it follows and waits
        with that handle's watcher thread, so forwarded callbacks run on
        several threads, one per followed box, possibly at the same time.
        Connect with `thread="main"` and call `psygnal.emit_queued()` to
        run them on the main thread. The handle that reading the field
        returns is a different one.

        A child process created with `fork` inherits the forwarding without
        its threads, and forwards nothing until it calls `follow` on this
        group, or on a group `follow` returned, with any field or none.
        That call follows the boxes the reference fields refer to at that
        moment, through the same groups, so callbacks connected before the
        fork keep receiving. Changes made before that call are not emitted.
        An error from attaching a box in that call is logged, and the box
        is tried again as after a reference change.

        Raises
        ------
        ValueError
            If the class has no field `field`.
        TypeError
            If `field` is not a reference field.
        BoxClosedError
            If the box is closed.
        """
        return self._sharedbox_follower.follow(field)

    def unfollow(self, field: str | None = None) -> None:
        """Stop forwarding for `field`, or all forwarding started through this group.

        With `field`, stop only the group that `follow(field)` returned, and
        leave running what `follow()` without a field started. The group is
        forgotten: callbacks connected to it receive nothing more, and a
        later [`follow`][sharedbox.BoxEvents.follow] returns a new group.
        Without `field`, stop both. The handles that reading the fields
        returned stay open. [`close`][sharedbox.SharedBox.close] on the box
        stops all its forwarding.

        Raises
        ------
        ValueError
            If the class has no field `field`.
        TypeError
            If `field` is not a reference field.
        """
        self._sharedbox_follower.unfollow(field)


def refuse_signal(label: str, group: object) -> NoReturn:
    raise TypeError(f"{label} has no value, so it has no signal")


def events_class(owner: type, layout: Layout) -> type[BoxEvents]:
    """Create a [`BoxEvents`][sharedbox.BoxEvents] subclass with one `(new, old)` signal per field of `owner`.

    It also gets the signal `metadata`, and a class with reference fields
    the signal `nested`. An attr field whose type is `None` gets a property
    that raises `TypeError`.
    """
    signals: dict[str, Any] = {
        spec.name: Signal(object, object) for spec in layout.watched
    }
    for spec in layout.fields:
        if spec.attr is not None and not spec.attr.has_value:
            signals[spec.name] = property(partial(refuse_signal, spec.label))
    signals["metadata"] = Signal(int)
    if layout.refs:
        signals["nested"] = Signal(tuple, object, object)
    return type(f"{owner.__name__}Events", (BoxEvents,), signals)
