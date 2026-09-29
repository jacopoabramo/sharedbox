"""Box classes declared under `from __future__ import annotations`, for test_refs."""

from __future__ import annotations

from sharedbox import SharedBox


class Link(SharedBox):
    value: int = 0
    next: Link | None = None
