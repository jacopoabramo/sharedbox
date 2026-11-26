import dataclasses

from sharedbox import SharedBox


class Settings(SharedBox, identity="capsule-consumer/settings/1"):
    """What the producer thread reads before each send.

    `count` is how many samples to send, 0 for no limit.
    """

    rate_hz: float = 1000.0
    count: int = 0


@dataclasses.dataclass(frozen=True)
class Sample:
    """One item the producer sends: its position and a value computed from it."""

    index: int
    value: float
