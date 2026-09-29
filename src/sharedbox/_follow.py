from __future__ import annotations

import logging
import threading
import weakref
from functools import partial
from typing import TYPE_CHECKING, Any

from ._native import BoxClosedError
from ._refs import attach_reference, shown

if TYPE_CHECKING:
    from ._box import SharedBox
    from ._events import BoxEvents, Watcher
    from ._layout import FieldSpec
    from ._native import Segment

logger = logging.getLogger("sharedbox")


class Follower:
    """What one events group forwards, and the box it forwards from.

    The events group of a box has a follower whose source is that box. Each
    group that [`follow`][sharedbox.BoxEvents.follow] returns has one whose
    source is its own handle on the box the reference field refers to.
    Every follower below one box's events group uses the lock of that
    group's follower.
    """

    __slots__ = (
        "__weakref__",
        "box",
        "cls",
        "group",
        "links",
        "lock",
        "outer",
        "ref",
        "spec",
        "top",
    )

    def __init__(
        self,
        top: Follower | None,
        spec: FieldSpec | None,
        cls: type[SharedBox],
        group: BoxEvents | None,
    ) -> None:
        self.top = self if top is None else top
        self.spec = spec
        # The class whose fields `follow` and `unfollow` accept: the shape of the group.
        self.cls = cls
        self.group = group
        self.box: SharedBox | None = None
        self.ref: Any = None
        self.outer: Segment | None = None
        self.links: dict[str, Follower] = {}
        self.lock = threading.Lock()

    def follow(self, field: str) -> BoxEvents:
        top = self.top
        assert top.outer is not None
        if top.outer.closed:
            raise BoxClosedError(f"box {top.outer.name!r} is closed")
        spec = self.ref_spec(field)
        with top.lock:
            source = self.source()
            link = self.links.get(spec.name)
            if link is None:
                assert spec.target is not None
                link = Follower(top, spec, spec.target, spec.target.__events_class__())
                assert link.group is not None
                link.group._sharedbox_follower = link
                value = None if source is None else read(*source, spec.name)
                self.links[spec.name] = link
                self.expand([(link, value)])
        assert link.group is not None
        return link.group

    def unfollow(self, field: str | None) -> None:
        if field is not None:
            self.ref_spec(field)
        with self.top.lock:
            if field is None:
                followers = list(self.links.values())
                self.links = {}
            else:
                link = self.links.pop(field, None)
                followers = [] if link is None else [link]
            boxes = [box for follower in followers for box in follower.detach()]
        close_all(boxes)

    def moved(self, spec: FieldSpec, value: Any, segment: Segment) -> None:
        """Follow the box that the field `spec` of `segment`, this follower's source, now refers to."""
        with self.top.lock:
            link = self.links.get(spec.name)
            if link is None or link.ref == value or not self.serves(segment):
                return
            boxes = link.detach()
        # Outside the lock: closing joins each box's watcher thread, which may be waiting for it.
        close_all(boxes)
        with self.top.lock:
            if self.serves(segment) and self.links.get(spec.name) is link:
                self.expand([(link, value)])

    def release(self) -> list[SharedBox]:
        """Stop all forwarding below this follower; the boxes it held are returned for closing."""
        with self.lock:
            return self.detach()

    def ref_spec(self, field: str) -> FieldSpec:
        spec = self.cls.__layout__.by_name.get(field)
        if spec is None:
            raise ValueError(f"{self.cls.__qualname__} has no field {field!r}")
        if spec.target is None:
            raise TypeError(f"{spec.label} is not a reference field")
        return spec

    def source(self) -> tuple[Segment, type[SharedBox]] | None:
        """The segment and class whose reference fields this follower's links follow."""
        if self.box is not None:
            return self.box._segment, type(self.box)
        if self.outer is not None:
            return self.outer, self.cls
        return None

    def serves(self, segment: Segment) -> bool:
        source = self.source()
        return source is not None and source[0] is segment and not segment.closed

    def link_children(
        self, segment: Segment, cls: type[SharedBox]
    ) -> list[tuple[Follower, Any]]:
        return [(link, read(segment, cls, name)) for name, link in self.links.items()]

    def expand(self, starts: list[tuple[Follower, Any]]) -> None:
        """Attach the box each follower's value refers to, then the boxes below it, depth first."""
        # Reversed, so fields are followed in declaration order.
        stack = starts[::-1]
        while stack:
            follower, value = stack.pop()
            follower.ref = value
            box = follower.box = follower.open(value)
            if box is None:
                continue
            segment, cls = box._segment, type(box)
            # Listening before the values below are read, so a later change is still delivered.
            box._watcher.listen(
                partial(relay, weakref.ref(follower), segment), cls.__layout__.fields
            )
            stack.extend(reversed(follower.link_children(segment, cls)))

    def open(self, value: Any) -> SharedBox | None:
        """A new handle on the box `value` refers to, or None when the field is empty."""
        if value is None:
            return None
        create_id, schema_hash, name = value
        assert self.spec is not None
        return attach_reference(self.spec, create_id, schema_hash, name)

    def detach(self) -> list[SharedBox]:
        """Stop following at and below this follower; the boxes it held are returned for closing."""
        boxes: list[SharedBox] = []
        stack = [self]
        while stack:
            follower = stack.pop()
            box, follower.box, follower.ref = follower.box, None, None
            if box is not None:
                boxes.append(box)
            stack.extend(follower.links.values())
        return boxes


def read(segment: Segment, cls: type[SharedBox], name: str) -> Any:
    return segment.get(cls.__layout__.by_name[name].index)


def relay(
    ref: weakref.ref[Follower], segment: Segment, spec: FieldSpec, new: Any, old: Any
) -> None:
    """Emit a change of the box on `segment` for its follower, then move what follows its references."""
    follower = ref()
    box = None if follower is None else follower.box
    if follower is None or box is None or box._segment is not segment:
        return
    try:
        if follower.group is not None and spec.name in follower.group:
            follower.group[spec.name].emit(shown(spec, new), shown(spec, old))
    except Exception:
        logger.exception("a callback for field %r raised", spec.name)
    if spec.target is not None:
        follower.moved(spec, new, segment)


def close_all(boxes: list[SharedBox]) -> None:
    """Close every box, then raise the first error any of them raised."""
    error: BaseException | None = None
    for box in boxes:
        try:
            box.close()
        except BaseException as exc:  # noqa: BLE001
            error = error or exc
    if error is not None:
        raise error


def box_events(cls: type[SharedBox], segment: Segment, watcher: Watcher) -> BoxEvents:
    """A new events group for the box of `cls` on `segment`, with the follower its `follow` uses."""
    group = cls.__events_class__()
    top = Follower(None, None, cls, group)
    top.outer = segment
    group._sharedbox_follower = top
    watcher.follower = top
    return group
