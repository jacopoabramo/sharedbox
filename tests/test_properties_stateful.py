import pickle
import uuid
from typing import Annotated, Any

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from sharedbox import (
    BoxClosedError,
    BoxRef,
    BrokenReferenceError,
    Capacity,
    SegmentNotFoundError,
    SharedBox,
)


class Model(SharedBox):
    a: int = 0
    b: float = 0.0
    s: Annotated[str, Capacity(8)] = ""
    flag: bool = False


VALUES: dict[str, st.SearchStrategy[Any]] = {
    "a": st.integers(-(2**63), 2**63 - 1),
    "b": st.floats(allow_nan=False),
    "s": st.text(max_size=8).filter(lambda t: len(t.encode()) <= 8),
    "flag": st.booleans(),
}
FIELDS = list(VALUES)
INDEX = {spec.name: spec.index for spec in Model.__layout__.fields}


class BoxMachine(RuleBasedStateMachine):
    """A box's handles against a dict of what the box should hold."""

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.model: dict[str, Any] = {}
        self.versions: dict[str, int] = {}
        self.handles: list[Model] = []
        self.closed: list[Model] = []

    @initialize()
    def create(self) -> None:
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        self.handles = [Model.create(self.name)]
        self.model = {"a": 0, "b": 0.0, "s": "", "flag": False}
        self.versions = dict.fromkeys(FIELDS, 0)

    @precondition(lambda self: len(self.handles) < 4)
    @rule()
    def attach(self) -> None:
        self.handles.append(Model.attach(self.name))

    @rule(data=st.data())
    def set_field(self, data: st.DataObject) -> None:
        handle = data.draw(st.sampled_from(self.handles))
        field = data.draw(st.sampled_from(FIELDS))
        value = data.draw(VALUES[field])
        setattr(handle, field, value)
        self.model[field] = value
        self.versions[field] += 1

    @rule(data=st.data())
    def update_several(self, data: st.DataObject) -> None:
        handle = data.draw(st.sampled_from(self.handles))
        fields = data.draw(st.lists(st.sampled_from(FIELDS), min_size=1, unique=True))
        values = {field: data.draw(VALUES[field]) for field in fields}
        handle.update(**values)
        self.model.update(values)
        for field in fields:
            self.versions[field] += 1

    @precondition(lambda self: len(self.handles) < 4)
    @rule(data=st.data())
    def pickle_round_trip(self, data: st.DataObject) -> None:
        handle = data.draw(st.sampled_from(self.handles))
        self.handles.append(pickle.loads(pickle.dumps(handle)))

    @precondition(lambda self: len(self.handles) > 1)
    @rule(data=st.data())
    def close_handle(self, data: st.DataObject) -> None:
        handle = data.draw(st.sampled_from(self.handles))
        self.handles.remove(handle)
        handle.close()
        self.closed.append(handle)

    @rule(data=st.data())
    def force_unlock_when_not_locked(self, data: st.DataObject) -> None:
        data.draw(st.sampled_from(self.handles)).force_unlock()

    @invariant()
    def every_handle_reads_the_model(self) -> None:
        for handle in self.handles:
            assert handle.snapshot() == self.model
            for field in FIELDS:
                assert getattr(handle, field) == self.model[field]

    @invariant()
    def versions_count_the_writes_of_each_field(self) -> None:
        for handle in self.handles:
            for field in FIELDS:
                assert handle._segment.version(INDEX[field]) == self.versions[field]

    @invariant()
    def closed_handles_refuse_use(self) -> None:
        for handle in self.closed:
            with pytest.raises(BoxClosedError):
                handle.snapshot()

    def teardown(self) -> None:
        for handle in self.handles:
            handle.close()
        if self.name:
            Model.unlink(self.name)
            # With every handle closed and the name removed, no segment is left to find.
            with pytest.raises(SegmentNotFoundError):
                Model.attach(self.name)


TestBoxMachine = BoxMachine.TestCase


class Node(SharedBox):
    value: int = 0
    link: "Node | None" = None
    spare: "Node | None" = None


NODES = st.integers(0, 2)
LINKS = ("link", "spare")
Link = tuple[int, int]


class GraphMachine(RuleBasedStateMachine):
    """Boxes that refer to each other, against a model of which box each field refers to.

    Removing a box first reopens every other box. That closes the boxes
    their reads attached, so on Windows, where a segment lives as long as
    any handle to it, the removed box's name is free again.
    """

    def __init__(self) -> None:
        super().__init__()
        self.prefix = f"sbtest-{uuid.uuid4().hex[:12]}"
        self.boxes: dict[int, Node] = {}
        self.ids: dict[int, int] = {}
        self.values: dict[int, int] = {}
        self.links: dict[int, dict[str, Link | None]] = {}
        self.returned: list[Node] = []

    def name(self, node: int) -> str:
        return f"{self.prefix}-{node}"

    def reopen_all_but(self, node: int) -> None:
        for other in list(self.boxes):
            if other != node:
                self.reopen(other)

    @rule(node=NODES)
    def create(self, node: int) -> None:
        """Create the box of `node` if it does not exist."""
        if node in self.boxes:
            return
        box = Node.create(self.name(node))
        self.boxes[node] = box
        # The create id is not public; the model needs it to build the BoxRef a snapshot reports.
        self.ids[node] = box._segment.create_id
        self.values[node] = 0
        self.links[node] = dict.fromkeys(LINKS)

    @rule(node=NODES, value=st.integers(-100, 100))
    def set_value(self, node: int, value: int) -> None:
        """Write the scalar field of `node`."""
        if node in self.boxes:
            self.boxes[node].value = value
            self.values[node] = value

    @rule(node=NODES, field=st.sampled_from(LINKS), to=NODES)
    def assign(self, node: int, field: str, to: int) -> None:
        """Point a reference field of `node` at the box of `to`."""
        if node in self.boxes and to in self.boxes:
            setattr(self.boxes[node], field, self.boxes[to])
            self.links[node][field] = (to, self.ids[to])

    @rule(node=NODES, field=st.sampled_from(LINKS))
    def empty(self, node: int, field: str) -> None:
        """Set a reference field of `node` to None."""
        if node in self.boxes:
            setattr(self.boxes[node], field, None)
            self.links[node][field] = None

    @rule(node=NODES)
    def remove(self, node: int) -> None:
        """Close every handle to the box of `node` and unlink its name."""
        if node not in self.boxes:
            return
        self.reopen_all_but(node)
        self.boxes.pop(node).close()
        Node.unlink(self.name(node))
        del self.values[node], self.links[node]

    @rule(node=NODES)
    def reopen(self, node: int) -> None:
        """Replace the handle of `node` with a new one, closing the old handle."""
        if node in self.boxes:
            fresh = Node.attach(self.name(node))
            self.boxes[node].close()
            self.boxes[node] = fresh

    def box_ref(self, link: Link) -> BoxRef:
        return BoxRef(self.name(link[0]), Node.__layout__.schema_hash, link[1])

    def broken(self, link: Link) -> str | None:
        target, create_id = link
        if target not in self.boxes:
            return "no longer exists"
        if self.ids[target] != create_id:
            return "was created again"
        return None

    def followed(self, node: int) -> dict[str, Any]:
        """Return the expected `snapshot(follow=True)` of `node`, or raise BrokenReferenceError where that call must."""
        seen = {(node, self.ids[node])}

        def visit(node: int) -> dict[str, Any]:
            values: dict[str, Any] = {"value": self.values[node]}
            for field, link in self.links[node].items():
                if link is None or link in seen:
                    values[field] = None if link is None else self.box_ref(link)
                    continue
                seen.add(link)
                if (why := self.broken(link)) is not None:
                    raise BrokenReferenceError(why)
                values[field] = visit(link[0])
            return values

        return visit(node)

    @invariant()
    def reads_match_the_model(self) -> None:
        """Check that snapshot() and every field read agree with the model."""
        for node, box in self.boxes.items():
            links = self.links[node]
            assert box.snapshot() == {
                "value": self.values[node],
                **{f: None if v is None else self.box_ref(v) for f, v in links.items()},
            }
            for field, link in links.items():
                if link is None:
                    assert getattr(box, field) is None
                elif (why := self.broken(link)) is not None:
                    with pytest.raises(BrokenReferenceError, match=why):
                        getattr(box, field)
                else:
                    inner = getattr(box, field)
                    assert (inner.name, inner.value) == (
                        self.name(link[0]),
                        self.values[link[0]],
                    )
                    self.returned.append(inner)

    @invariant()
    def snapshot_follow_matches_the_model(self) -> None:
        """Check that snapshot(follow=True) ends on every loop and matches the model."""
        for node, box in self.boxes.items():
            try:
                expected = self.followed(node)
            except BrokenReferenceError as error:
                with pytest.raises(BrokenReferenceError, match=str(error)):
                    box.snapshot(follow=True)
            else:
                assert box.snapshot(follow=True) == expected

    def teardown(self) -> None:
        for box in self.boxes.values():
            box.close()
        # Every box a read returned sits in some handle's cache, which close() empties.
        assert all(inner.closed for inner in self.returned)
        for node in self.boxes:
            Node.unlink(self.name(node))
            with pytest.raises(SegmentNotFoundError):
                Node.attach(self.name(node))


TestGraphMachine = GraphMachine.TestCase
