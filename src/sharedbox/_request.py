"""Call fields: a box field through which any process asks the process that serves it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Generator
from concurrent.futures import Future, InvalidStateError
from enum import IntEnum
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Generic,
    NoReturn,
    ParamSpec,
    TypeVar,
    overload,
)

from ._native import BoxClosedError

if TYPE_CHECKING:
    from ._box import SharedBox
    from ._events import Watcher
    from ._layout import FieldSpec

P = ParamSpec("P")
R = TypeVar("R")
# Seconds between checks that the server of a pending request still runs, as a stream reader checks its sender.
LIVENESS_DELAY: Final = 0.1
ERROR_BYTES: Final = 256
logger = logging.getLogger("sharedbox")


class RequestError(Exception):
    """The request failed: the handler raised, the server stopped, or four later answers overwrote its answer."""


class RequestSupersededError(RequestError):
    """A later request replaced this one before the server took it."""


class ServerExitedError(RequestError):
    """The process serving the field ended while the request waited."""


class AlreadyServedError(RuntimeError):
    """A live process serves the field already, this one included."""


class Outcome(IntEnum):
    """What became of a request, as `Segment.request_answer_of` reports it."""

    PENDING = 0
    ANSWERED = 1
    FAILED = 2
    SUPERSEDED = 3
    OVERWRITTEN = 4
    SERVER_EXITED = 5


def error_text(error: BaseException) -> str:
    """Return `error` as a failed answer's text: its type and message, cut to 256 bytes on a UTF-8 boundary."""
    text = f"{type(error).__name__}: {error}".encode(errors="backslashreplace")
    text = text[:ERROR_BYTES]
    return text.decode(errors="ignore")


class Call(Generic[P, R]):
    """A box field through which a process sends a typed request to the process that serves it.

    Annotate a field `Call[[A, ...], R]`, where the list holds the argument
    types and `R` the result type, or `None` for no result, and declare it
    with [`call`][sharedbox.call]. The field holds no value: reading it
    from a box gives the `Call` bound to that box.
    Arguments and result may be anything a record member may be, except a
    [`Timed`][sharedbox.Timed], a `Call` or a reference to a box.

    Calling it sends a request at once and returns a
    [`RequestFuture`][sharedbox.RequestFuture] of the answer;
    [`serve`][sharedbox.Call.serve] answers the requests.
    """

    __slots__ = ("_box", "_spec")

    def __init__(self, box: SharedBox, spec: FieldSpec) -> None:
        self._box = box
        self._spec = spec

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> RequestFuture[R]:
        """Send a request with `args` and return the future of its answer.

        The request is sent whether or not anyone waits for the future, and
        replaces an earlier one the server has not taken yet.

        Raises
        ------
        TypeError
            If an argument is given by keyword, or the arguments do not
            match the field's argument types.
        ValueError
            If an argument does not fit its type.
        OverflowError
            If a number is out of the range of its type.
        BoxClosedError
            If the box is closed.
        """
        if kwargs:
            raise TypeError(f"{self._spec.label} takes positional arguments only")
        segment = self._box._segment
        index = self._spec.index
        since = segment.version(index)
        number = segment.request_send(index, args, type(self._box).__layout__.types)
        return self._box._watcher.call_future(self._spec, number, since)

    @overload
    def serve(self, handler: Callable[P, Awaitable[R]]) -> RequestServer: ...
    @overload
    def serve(self, handler: Callable[P, R]) -> RequestServer: ...
    def serve(self, handler: Callable[P, Any]) -> RequestServer:
        """Answer the field's requests with `handler` until the server is stopped or the box closed.

        A sync handler runs on a thread of its own; an async one runs as a
        task on the event loop running at this call. The handler handles
        one request at a time, and of the requests sent meanwhile only the
        newest is handled next. Its return value is the answer; an
        exception it raises is logged to the `sharedbox` logger and reaches
        the caller as [`RequestError`][sharedbox.RequestError].

        Raises
        ------
        AlreadyServedError
            If a live process serves the field already, this one included.
        RuntimeError
            If `handler` is async and no event loop is running.
        BoxClosedError
            If the box is closed.
        """
        return self._box._watcher.serve(self._spec, handler)


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


class RequestFuture(Future[R]):
    """The answer to one request sent through a [`Call`][sharedbox.Call] field.

    A `concurrent.futures.Future`, so `.result(timeout)` waits in any
    thread, and it can be awaited in a running event loop. The box's
    watcher thread completes it, and closing the box cancels it.
    Cancelling it stops the wait only: the request stays sent.
    """

    def __init__(
        self, watcher: Watcher, field: FieldSpec, number: int, since: int
    ) -> None:
        super().__init__()
        self._watcher = watcher
        self._field = field
        self._number = number
        self._since = since
        self._notified = False

    def cancel(self) -> bool:
        """Stop waiting; False if the future already has its answer, True otherwise."""
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

    def result(self, timeout: float | None = None) -> R:
        """Block until the answer arrives and return the handler's result.

        Raises
        ------
        TimeoutError
            If no answer arrived within `timeout` seconds.
        RequestError
            If the request failed; see its subclasses.
        concurrent.futures.CancelledError
            If the future was cancelled, or the box closed.
        """
        try:
            return super().result(timeout)
        except TimeoutError:
            if self.done():
                raise
            raise TimeoutError(
                f"{self._field.label}: no answer to request {self._number} "
                f"within {timeout} s"
            ) from None

    def __await__(self) -> Generator[Any, None, R]:
        return asyncio.wrap_future(self).__await__()

    def _fail(self, error: BaseException) -> None:
        with contextlib.suppress(InvalidStateError):
            self.set_exception(error)

    def _complete(self, outcome: int, value: Any) -> None:
        label = self._field.label
        error: RequestError
        match outcome:
            case Outcome.ANSWERED:
                with contextlib.suppress(InvalidStateError):
                    self.set_result(value)
                return
            case Outcome.FAILED:
                error = RequestError(f"{label}: {value}")
            case Outcome.SUPERSEDED:
                error = RequestSupersededError(
                    f"{label}: a later request replaced request {self._number} "
                    "before the server took it"
                )
            case Outcome.OVERWRITTEN:
                error = RequestError(
                    f"{label}: four later answers overwrote the answer to "
                    f"request {self._number}"
                )
            case Outcome.SERVER_EXITED:
                error = ServerExitedError(f"{label}: the server exited")
        self._fail(error)


class RequestServer:
    """Answers the requests of one call field; [`Call.serve`][sharedbox.Call.serve] returns it."""

    def __init__(
        self,
        watcher: Watcher,
        field: FieldSpec,
        handler: Callable[..., Any],
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        self._watcher = watcher
        self._field = field
        self._handler = handler
        self._loop = loop
        # Held while checking for a stop and taking, so no take follows the release.
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        # The ident of the thread inside _take, which a _halt on that same thread must not wait for.
        self._taker: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        if loop is None:
            self._thread = threading.Thread(
                target=self._run,
                name=f"sharedbox-serve-{field.label}",
                daemon=True,
            )

    def stop(self) -> None:
        """Stop answering and free the field for another server.

        A sync handler that is running is waited for, unless this is called
        from the handler itself or from a callback on the box's watcher
        thread. For an async handler, cancelling its task is requested of
        the event loop, which runs the cancellation after this returns. A
        request taken and not answered yet fails with "the server stopped".
        Calling it again does nothing.
        """
        self._halt(join=not self._watcher.on_watcher_thread())

    def _start(self) -> None:
        """Start serving, answering a request sent before [`serve`][sharedbox.Call.serve] too."""
        if self._thread is None:
            self._kick()
        else:
            self._wake.set()
            self._thread.start()

    def _kick(self) -> None:
        """Take and answer the newest request, if one is waiting."""
        if self._loop is None:
            self._wake.set()
            return
        try:
            self._loop.call_soon_threadsafe(self._start_task)
        except RuntimeError:
            self._halt(join=False)

    def _halt(self, join: bool, cancel: bool = True) -> None:
        """Stop serving and release the field; join a running sync thread only if `join`."""
        # A finalizer that garbage collection runs inside this thread's own _take
        # must not wait for the lock that _take holds; _take answers what it took.
        if self._taker == threading.get_ident():
            self._stopped.set()
        else:
            with self._lock:
                if self._stopped.is_set():
                    return
                self._stopped.set()
        self._watcher.forget(self)
        if self._loop is not None:
            if cancel:
                with contextlib.suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(self._cancel_task)
        else:
            self._wake.set()
            if join:
                self._join()
        self._release()

    def _join(self) -> None:
        """Wait for a sync server's thread to end, unless this is that thread."""
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            with contextlib.suppress(RuntimeError):
                thread.join()

    def _release(self) -> None:
        with contextlib.suppress(BoxClosedError):
            self._watcher._segment.serve_release(self._field.index)

    def _after_fork(self) -> None:
        """Stop serving in a child created by `fork`, without releasing the parent's field."""
        self._lock = threading.Lock()
        self._taker = None
        self._stopped = threading.Event()
        self._stopped.set()

    def _take(self) -> tuple[int, tuple[Any, ...]] | None:
        with self._lock:
            if self._stopped.is_set():
                return None
            self._taker = threading.get_ident()
            try:
                request = self._watcher._segment.request_take(
                    self._field.index, self._watcher._types
                )
            finally:
                self._taker = None
            if self._stopped.is_set():
                if request is not None:
                    with contextlib.suppress(BoxClosedError):
                        self._answer(request[0], False, "the server stopped")
                self._release()
                return None
            return request

    def _answer(self, number: int, ok: bool, value: Any) -> None:
        segment, types = self._watcher._segment, self._watcher._types
        if ok:
            try:
                segment.request_answer(self._field.index, number, True, value, types)
                return
            except Exception as error:
                logger.exception("the result of %s cannot be stored", self._field.label)
                value = error_text(error)
        segment.request_answer(self._field.index, number, False, value, types)

    def _fail(self, number: int, error: BaseException) -> None:
        logger.exception("the handler of %s raised", self._field.label)
        self._answer(number, False, error_text(error))

    def _failed_serving(self, number: int | None, error: Exception) -> None:
        """Log an error of the serving loop and answer the request it had taken, if any, as failed."""
        logger.exception("serving %s failed", self._field.label)
        if number is not None:
            try:
                self._answer(number, False, error_text(error))
            except Exception:
                logger.exception(
                    "answering request %s of %s failed", number, self._field.label
                )

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stopped.is_set():
                return
            number: int | None = None
            try:
                while (request := self._take()) is not None:
                    number, arguments = request
                    try:
                        result = self._handler(*arguments)
                    except Exception as error:
                        self._fail(number, error)
                    except BaseException as error:
                        self._fail(number, error)
                        self._halt(join=False)
                        raise
                    else:
                        self._answer(number, True, result)
                    number = None
            except BoxClosedError:
                return
            except Exception as error:
                self._failed_serving(number, error)

    def _start_task(self) -> None:
        if not self._stopped.is_set() and (self._task is None or self._task.done()):
            assert self._loop is not None
            self._task = self._loop.create_task(
                self._serve(), name=f"sharedbox-serve-{self._field.label}"
            )

    def _loop_closed(self) -> bool:
        """Return whether this is an async server whose event loop has closed."""
        return self._loop is not None and self._loop.is_closed()

    def _cancel_task(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _serve(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        number: int | None = None
        try:
            while (request := self._take()) is not None:
                number, arguments = request
                try:
                    result = await self._handler(*arguments)
                except asyncio.CancelledError as error:
                    # Raised by the handler itself unless this task was cancelled.
                    if task.cancelling():
                        raise
                    self._fail(number, error)
                except Exception as error:
                    self._fail(number, error)
                else:
                    self._answer(number, True, result)
                number = None
        except asyncio.CancelledError:
            if number is not None:
                with contextlib.suppress(BoxClosedError):
                    self._answer(number, False, "the handler was cancelled")
            self._halt(join=False, cancel=False)
            raise
        except BoxClosedError:
            return
        except Exception as error:
            self._failed_serving(number, error)
