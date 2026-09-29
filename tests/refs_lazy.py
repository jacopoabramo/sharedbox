"""A box class that names itself in an unquoted annotation, which Python 3.14 evaluates lazily."""

from sharedbox import SharedBox


class Chain(SharedBox):
    value: int = 0
    next: Chain | None = None  # noqa: F821
