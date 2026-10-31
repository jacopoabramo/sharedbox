"""Whether a segment exists under a name and whether its creator still runs."""

from __future__ import annotations

from enum import StrEnum

from ._box import check_name
from ._native import segment_status as native_status


class SegmentStatus(StrEnum):
    """What [`segment_status`][sharedbox.segment_status] found under a name."""

    MISSING = "missing"
    """No box or stream is published under the name."""
    RUNNING = "running"
    """A box or stream is published under the name, and the process that
    created it still runs or cannot be judged from this process, such as one
    in another pid namespace."""
    CREATOR_EXITED = "creator_exited"
    """A box or stream is published under the name, and the process that
    created it has exited."""


def segment_status(name: str) -> SegmentStatus:
    """Say whether a box or stream exists under `name` and whether its creator still runs.

    A segment that another process is creating is waited for up to 1 s, and
    one still not published then counts as missing.

    Raises
    ------
    ValueError
        If `name` is not segments of `[A-Za-z0-9_-]` joined by `:`, at most
        240 characters.
    SchemaMismatchError
        If the segment uses a core version of the layout this sharedbox
        cannot read.
    OSError
        If the operating system refuses to open the segment on Linux, such
        as one of another user.
    RuntimeError
        If the operating system refuses to open the segment on Windows.

    Notes
    -----
    On Windows a segment goes away with its last handle, so
    `CREATOR_EXITED` appears only while another process still holds the
    segment open.
    """
    return SegmentStatus(native_status(check_name(name)))
