"""A value with the time it was taken."""

from __future__ import annotations

from typing import Final, Generic, NamedTuple, TypeVar

T = TypeVar("T")

NOW: Final[int] = -(2**63)


class Timed(NamedTuple, Generic[T]):
    """A value with a timestamp in nanoseconds since the Unix epoch.

    A box field or stream item annotated `Timed[T]` stores `value` as a `T`
    followed by `timestamp_ns`. The timestamp `-2**63` is reserved: a box
    write replaces it with the system clock once it holds the box's lock, so
    the stored timestamps follow the order of the writes unless the clock is
    set back, and a stream send replaces it before it publishes the item. No
    read returns it.
    """

    value: T
    timestamp_ns: int

    @property
    def timestamp(self) -> float:
        """The timestamp in seconds since the Unix epoch."""
        return self.timestamp_ns / 1e9

    @classmethod
    def now(cls, value: T) -> Timed[T]:
        """Return `value` with the mark that asks the write to stamp it with the clock."""
        return cls(value, NOW)
