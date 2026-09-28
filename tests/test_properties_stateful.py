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

from sharedbox import BoxClosedError, Capacity, SegmentNotFoundError, SharedBox


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
