from __future__ import annotations

import logging
import threading
import weakref
from functools import partial
from typing import TYPE_CHECKING, Any

from ._events import on_watcher_thread
from ._native import BoxClosedError
from ._refs import (
    BrokenReferenceError,
    UnknownBoxClassError,
    attach_reference,
    shown,
)

if TYPE_CHECKING:
    from psygnal import SignalInstance

    from ._box import SharedBox
    from ._events import BoxEvents, Watcher
    from ._layout import FieldSpec
    from ._native import Segment

logger = logging.getLogger("sharedbox")


class Follower:
    """What one events group forwards, and the box it forwards from.

    The events group of a box has a follower whose source is that box. Each
    group that [`follow`][sharedbox.BoxEvents.follow] returns has one whose
    source is its own handle on the box the reference field refers to, and
    so has each box that a `follow()` without a field reaches, which emits
    on the `nested` signal of the group that started it. Every follower
    below one box's events group uses the lock of that group's follower.
    """

    __slots__ = (
        "__weakref__",
        "box",
        "cls",
        "failed",
        "forked",
        "group",
        "links",
        "lock",
        "nested",
        "outer",
        "path",
        "ref",
        "seen",
        "spec",
        "top",
        "tree",
        "watcher",
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
        self.tree: dict[str, Follower] | None = None
        # Each box the tree follows, with the nodes that skipped it as already followed.
        self.seen: dict[tuple[str, int], list[Follower]] = {}
        # On the top follower: the nodes whose last attach raised, tried again on every move.
        self.failed: dict[Follower, None] = {}
        self.nested: SignalInstance | None = None
        self.path: tuple[str, ...] = ()
        self.lock = threading.Lock()
        # Set in a child created by `fork`, where the next `follow` rebuilds forwarding.
        self.forked = False
        # On the top follower: the watcher of the box that owns its events group.
        self.watcher: Watcher | None = None

    def follow(self, field: str | None) -> BoxEvents | None:
        top = self.top
        assert top.outer is not None
        if top.outer.closed:
            raise BoxClosedError(f"box {top.outer.name!r} is closed")
        spec = None if field is None else self.ref_spec(field)
        if top.forked:
            with top.lock:
                stale = top.rebuild() if top.forked else []
            # Not joined: their threads do not exist in a child created by `fork`.
            close_all(stale, wait=False)
        boxes: list[SharedBox] = []
        group: BoxEvents | None = None
        try:
            with top.lock:
                source = self.source()
                starts: list[tuple[Follower, Any]] = []
                try:
                    if spec is None:
                        if self.tree is not None:
                            return None
                        self.tree = {}
                        if source is not None:
                            starts = self.tree_children(*source)
                    else:
                        link = self.links.get(spec.name)
                        if link is not None:
                            return link.group
                        assert spec.target is not None
                        link = Follower(
                            top, spec, spec.target, spec.target.__events_class__()
                        )
                        assert link.group is not None
                        link.group._sharedbox_follower = link
                        value = (
                            None
                            if source is None
                            else read(source[1], source[2], spec.name)
                        )
                        self.links[spec.name] = link
                        starts = [(link, value)]
                        group = link.group
                    self.expand(starts)
                except BaseException:
                    # Forget what this call started, so that the next call starts it again.
                    if spec is None:
                        self.tree = None
                    else:
                        self.links.pop(spec.name, None)
                    boxes = [box for child, _ in starts for box in child.detach()]
                    raise
        except BaseException:
            # Outside the lock, as in `moved`.
            close_all(boxes, wait=not on_watcher_thread())
            raise
        return group

    def unfollow(self, field: str | None) -> None:
        if field is not None:
            self.ref_spec(field)
        with self.top.lock:
            if field is None:
                followers = [*self.links.values(), *(self.tree or {}).values()]
                self.links, self.tree = {}, None
            else:
                link = self.links.pop(field, None)
                followers = [] if link is None else [link]
            boxes = [box for follower in followers for box in follower.detach()]
        close_all(boxes, wait=not on_watcher_thread())

    def moved(self, spec: FieldSpec, value: Any, segment: Segment) -> None:
        """Follow the box that the field `spec` of `segment`, this follower's source, now refers to."""
        with self.top.lock:
            # Skipped in a forked child: its next `follow` rebuilds from the values the watcher saw.
            if self.top.forked or not self.serves(segment):
                return
            children = [
                child
                for child in (
                    self.links.get(spec.name),
                    (self.tree or {}).get(spec.name),
                )
                if child is not None and child.ref != value
            ]
            waiting: list[Follower] = []
            boxes = [box for child in children for box in child.detach(waiting)]
        # Outside the lock: closing joins each box's watcher thread, which may be waiting for it.
        # Joined even on a watcher thread, so the old box forwards nothing after the move.
        close_all(boxes, wait=True)
        with self.top.lock:
            starts = (
                [
                    (child, value)
                    for child in children
                    if child is self.links.get(spec.name)
                    or child is (self.tree or {}).get(spec.name)
                ]
                if self.serves(segment)
                else []
            )
            # Even if this follower's source moved meanwhile, since these nodes are elsewhere in the tree.
            retry = [*waiting, *self.top.failed]
            self.top.failed.clear()
            starts += [
                (node, node.ref)
                for node in retry
                if node.box is None and node.ref is not None
            ]
            self.expand(starts)

    def release(self) -> list[SharedBox]:
        """Stop all forwarding below this follower; the boxes it held are returned for closing."""
        with self.lock:
            return self.detach()

    def after_fork(self) -> None:
        """Replace the lock, which another thread of the parent may have held, and make the next `follow` rebuild forwarding."""
        self.lock = threading.Lock()
        self.forked = True

    def rebuild(self) -> list[SharedBox]:
        """Follow again, from the values of the reference fields the box's watcher last saw, everything forwarded through this box's events group.

        In a child created by `fork`, a move that another thread of the
        parent had begun can leave forwarding half changed. Afterwards it
        follows what a new [`follow`][sharedbox.BoxEvents.follow] would,
        through the same groups, so callbacks connected before stay
        connected. An error in following a box is logged. The boxes
        forwarding held before are returned for closing.
        """
        assert self.outer is not None and self.watcher is not None
        boxes = self.detach()
        self.failed.clear()
        try:
            starts = self.link_children(self.cls, self.watcher)
            if self.tree is not None:
                starts += self.tree_children(self.outer, self.cls, self.watcher)
            # Only once the values are read, so that a failed read leaves the next `follow` to rebuild again.
            self.forked = False
            self.expand(starts)
        except Exception:
            logger.exception("following the referenced boxes again after fork failed")
        finally:
            self.watcher.resume()
        return boxes

    def ref_spec(self, field: str) -> FieldSpec:
        spec = self.cls.__layout__.by_name.get(field)
        if spec is None:
            raise ValueError(f"{self.cls.__qualname__} has no field {field!r}")
        if spec.target is None:
            raise TypeError(f"{spec.label} is not a reference field")
        return spec

    def source(self) -> tuple[Segment, type[SharedBox], Watcher] | None:
        """The segment, class and watcher of the box whose reference fields this follower's links and tree follow."""
        if self.box is not None:
            return self.box._segment, type(self.box), self.box._watcher
        if self.outer is not None:
            assert self.watcher is not None
            return self.outer, self.cls, self.watcher
        return None

    def serves(self, segment: Segment) -> bool:
        source = self.source()
        return source is not None and source[0] is segment and not segment.closed

    def link_children(
        self, cls: type[SharedBox], watcher: Watcher
    ) -> list[tuple[Follower, Any]]:
        return [(link, read(cls, watcher, name)) for name, link in self.links.items()]

    def tree_children(
        self, segment: Segment, cls: type[SharedBox], watcher: Watcher
    ) -> list[tuple[Follower, Any]]:
        """A new node for each reference field of the source, with the value its watcher last saw."""
        if self.nested is None:
            # This follower starts the tree: its own box counts as followed.
            self.seen = {(segment.name, segment.create_id): []}
            specs = self.cls.__layout__.refs
        else:
            specs = cls.__layout__.refs
        self.tree = {}
        children: list[tuple[Follower, Any]] = []
        for spec in specs:
            assert spec.target is not None
            node = Follower(self.top, spec, spec.target, None)
            if self.nested is not None:
                node.nested = self.nested
            else:
                assert self.group is not None
                node.nested = self.group["nested"]
            node.path = (*self.path, spec.name)
            node.seen = self.seen
            node.tree = {}
            self.tree[spec.name] = node
            children.append((node, read(cls, watcher, spec.name)))
        return children

    def expand(self, starts: list[tuple[Follower, Any]]) -> None:
        """Attach the box each follower's value refers to, then the boxes below it, depth first.

        If following a box raises, this closes that box, goes on with the
        others, logs every later error and raises the first at the end. The
        follower that failed keeps its value and follows nothing until that
        value changes, or until any reference field below the same events
        group changes, when it is tried again.
        """
        error: Exception | None = None
        # Reversed, so fields are followed in declaration order, as snapshot(follow=True) reads them.
        stack = starts[::-1]
        while stack:
            follower, value = stack.pop()
            follower.ref = value
            try:
                box = follower.box = follower.open(value)
                if box is None:
                    continue
                segment, cls = box._segment, type(box)
                # Listening first, so the values below are the ones the watcher compares later changes with.
                box._watcher.listen(
                    partial(relay, weakref.ref(follower), segment),
                    cls.__layout__.fields,
                )
                children = follower.link_children(cls, box._watcher)
                if follower.tree is not None:
                    children += follower.tree_children(segment, cls, box._watcher)
            except Exception as exc:
                # Not joined: the box's watcher thread may be waiting for the lock held here.
                # No node waits on the box yet, since nothing else ran since it was opened.
                close_all(follower.detach(), wait=False)
                follower.ref = value
                self.top.failed[follower] = None
                if error is None:
                    error = exc
                else:
                    logger.exception("following box %r failed", value[2])
                continue
            stack.extend(reversed(children))
        if error is not None:
            raise error

    def open(self, value: Any) -> SharedBox | None:
        """A new handle on the box `value` refers to, or None if there is none to follow."""
        if value is None:
            return None
        create_id, schema_hash, name = value
        if self.nested is not None:
            waiting = self.seen.get((name, create_id))
            if waiting is not None:
                if self not in waiting:
                    waiting.append(self)
                return None
        assert self.spec is not None
        try:
            box = attach_reference(self.spec, create_id, schema_hash, name)
        except (BrokenReferenceError, UnknownBoxClassError) as error:
            logger.warning("%s; forwarding nothing until it is assigned again", error)
            return None
        if self.nested is not None:
            self.seen[name, create_id] = []
        return box

    def detach(self, waiting: list[Follower] | None = None) -> list[SharedBox]:
        """Stop following at and below this follower; the boxes it held are returned for closing.

        Parameters
        ----------
        waiting
            Receives the nodes elsewhere in the tree that skipped a box
            released here because it was already followed.
        """
        boxes: list[SharedBox] = []
        stack = [self]
        while stack:
            follower = stack.pop()
            box, ref = follower.box, follower.ref
            follower.box = follower.ref = None
            follower.top.failed.pop(follower, None)
            if box is not None:
                boxes.append(box)
                if follower.nested is not None:
                    skipped = follower.seen.pop((box.name, box._segment.create_id), [])
                    if waiting is not None:
                        waiting += skipped
            elif follower.nested is not None and ref is not None:
                skipped = follower.seen.get((ref[2], ref[0]), [])
                if follower in skipped:
                    skipped.remove(follower)
            stack.extend(follower.links.values())
            if follower.tree:
                stack.extend(follower.tree.values())
                follower.tree = {}
        return boxes


def read(cls: type[SharedBox], watcher: Watcher, name: str) -> Any:
    # Not from the segment: a value the watcher has not seen yet may change back unreported.
    return watcher.last(cls.__layout__.by_name[name])


def relay(
    ref: weakref.ref[Follower], segment: Segment, spec: FieldSpec, new: Any, old: Any
) -> None:
    """Emit a change of the box on `segment` for its follower, then move what follows its references."""
    follower = ref()
    box = None if follower is None else follower.box
    if follower is None or box is None or box._segment is not segment:
        return
    try:
        if follower.nested is not None:
            follower.nested.emit(
                (*follower.path, spec.name), shown(spec, new), shown(spec, old)
            )
        elif follower.group is not None and spec.name in follower.group:
            follower.group[spec.name].emit(shown(spec, new), shown(spec, old))
    except Exception:
        logger.exception("a callback for field %r raised", spec.name)
    if spec.target is not None:
        follower.moved(spec, new, segment)


def close_all(boxes: list[SharedBox], wait: bool) -> None:
    """Close every box, then raise the first error any of them raised.

    Parameters
    ----------
    wait
        If false, do not join the boxes' watcher threads.
    """
    error: BaseException | None = None
    for box in boxes:
        try:
            box._close(wait)
        except BaseException as exc:
            error = error or exc
    if error is not None:
        raise error


def box_events(cls: type[SharedBox], segment: Segment, watcher: Watcher) -> BoxEvents:
    """A new events group for the box of `cls` on `segment`, with the follower its `follow` uses."""
    group = cls.__events_class__()
    top = Follower(None, None, cls, group)
    top.outer = segment
    top.watcher = watcher
    group._sharedbox_follower = top
    watcher.follower = top
    return group
