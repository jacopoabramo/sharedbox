from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Final, NoReturn

import cbor2

from ._native import MetadataGoneError

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

M = TypeVar("M", covariant=True, bound=Mapping[str, Any], default=Mapping[str, Any])  # noqa: PLC0105
V = TypeVar("V")

MAX_DEPTH: Final = 32
INT_MIN: Final = -(2**64)
INT_MAX: Final = 2**64 - 1
CACHE_SIZE: Final = 32


def _plain(value: object, depth: int) -> object:
    match value:
        case None | bool() | float() | str() | bytes():
            return value
        case int():
            if not INT_MIN <= value <= INT_MAX:
                raise ValueError(
                    f"metadata integer {value} is outside -2**64 to 2**64 - 1"
                )
            return value
        case Mapping():
            if depth >= MAX_DEPTH:
                raise ValueError(f"metadata nests deeper than {MAX_DEPTH} levels")
            plain: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(
                        f"metadata map keys must be str, not {type(key).__name__}"
                    )
                plain[key] = _plain(item, depth + 1)
            return plain
        case list() | tuple():
            if depth >= MAX_DEPTH:
                raise ValueError(f"metadata nests deeper than {MAX_DEPTH} levels")
            return [_plain(item, depth + 1) for item in value]
        case _:
            raise TypeError(f"metadata cannot hold a {type(value).__name__}")


def encode(value: object) -> bytes:
    """Return the canonical CBOR bytes of a metadata document.

    A document is `None`, a `bool`, an `int` from -2**64 to 2**64 - 1, a
    `float`, a `str`, `bytes`, a mapping with `str` keys, or a `list` or
    `tuple`, nested up to 32 levels of arrays and maps. Equal documents
    give equal bytes whatever the order of a mapping's keys.

    Raises
    ------
    TypeError
        If the document holds another type, or a mapping key is not a `str`.
    ValueError
        If an integer is out of range, the nesting is too deep, or a `str`
        holds a lone surrogate (`UnicodeEncodeError`).
    """
    return cbor2.dumps(_plain(value, 0), canonical=True)


class ReadOnlyDict(dict[str, Any]):
    """A map of a metadata document as it is read back: a `dict` that refuses every change.

    Every read of one version shares one decoded copy, which is why it
    cannot be changed. It pickles, deep-copies and converts to JSON as a
    `dict` does, and a pickled or deep-copied one is read-only too.
    `dict(value)` gives a copy of the top level that can be changed.
    """

    __slots__ = ()

    def _refuse(self, *args: object, **kwargs: object) -> NoReturn:
        raise TypeError(
            "metadata read from a box or a stream is read-only; build a new "
            "value and add it as the next version"
        )

    __setitem__ = _refuse
    __delitem__ = _refuse
    __ior__ = _refuse
    clear = _refuse
    pop = _refuse
    popitem = _refuse
    setdefault = _refuse
    update = _refuse

    def __reduce__(self) -> tuple[type[ReadOnlyDict], tuple[dict[str, Any]]]:
        return type(self), (dict(self),)


def _freeze(value: object) -> object:
    match value:
        case dict():
            return ReadOnlyDict({key: _freeze(item) for key, item in value.items()})
        case list():
            return tuple(_freeze(item) for item in value)
        case _:
            return value


def decode(data: bytes) -> Any:
    """Return the document stored in `data`, maps as read-only dicts and arrays as tuples."""
    return _freeze(cbor2.loads(data))


def newest(head: Callable[[], int], version_of: Callable[[int], V]) -> V | None:
    """Return `version_of` the version `head` gives, or None while `head` gives 0.

    Two adds after `head` was read can reclaim its version, so a
    `MetadataGoneError` is retried with the new head while the head moves,
    and raised once it stays.
    """
    version = head()
    while version != 0:
        try:
            return version_of(version)
        except MetadataGoneError:
            latest = head()
            if latest == version:
                raise
            version = latest
    return None


class DocumentCache:
    """Decoded documents by creation id and version, the 32 most recently used kept."""

    __slots__ = ("_lock", "_values")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: OrderedDict[tuple[int, int], Any] = OrderedDict()

    def get(self, create_id: int, version: int, read: Callable[[], bytes]) -> Any:
        """Return the decoded document, calling `read` for its bytes when it is not cached.

        Two threads that miss together may both read and decode.
        """
        key = (create_id, version)
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
                return self._values[key]
        value = decode(read())
        with self._lock:
            self._values[key] = value
            while len(self._values) > CACHE_SIZE:
                self._values.popitem(last=False)
        return value
