import contextlib
import dataclasses
import gc
import inspect
import multiprocessing as mp
import pickle
import queue
import struct
import subprocess
import sys
import threading
import types
import weakref
from collections.abc import Callable
from dataclasses import KW_ONLY, InitVar, dataclass
from pathlib import Path
from typing import Any, Optional, Union

import pytest
from refs_future import Link

from sharedbox import (
    BoxClosedError,
    BoxRef,
    BrokenReferenceError,
    SchemaMismatchError,
    SharedBox,
    UnknownBoxClassError,
    field,
)

if sys.platform.startswith("linux"):
    import resource


class Motor(SharedBox, identity="sbtest/motor"):
    position: int = 0


class Stage(SharedBox):
    target: int = 0
    motor: Motor | None = None


class Node(SharedBox):
    value: int = 0
    link: "Node | None" = None


class Holder(SharedBox):
    motor: Optional[Motor]  # noqa: UP045


class Other(SharedBox):
    x: int = 0


class MotorCopy(SharedBox, identity="sbtest/motor"):
    position: int = 0


class FastMotor(Motor):
    pass


class Pair(SharedBox):
    first: Motor | None = None
    second: Motor | None = None


def test_an_optional_reference_without_a_default_is_required(
    names: Callable[[str], str],
) -> None:
    """Check that an optional reference field without a default must be given at creation."""
    with pytest.raises(TypeError, match=r"missing value\(s\) for motor"):
        Holder.create(names("h"))
    with Holder.create(names("h"), None) as holder:
        assert holder.motor is None


class Rig(SharedBox):
    base: Motor
    spare: Motor | None
    extra: Motor | None = None
    backup: Motor | None = field(default_factory=lambda: None)
    hidden: Motor | None = field(default=None, init=False)
    _: KW_ONLY
    tail: Motor


@dataclass
class PlainRig:
    base: Motor
    spare: Motor | None
    extra: Motor | None = None
    backup: Motor | None = dataclasses.field(default_factory=lambda: None)
    hidden: Motor | None = dataclasses.field(default=None, init=False)
    _: KW_ONLY
    tail: Motor


def test_reference_fields_take_defaults_as_in_a_dataclass(
    names: Callable[[str], str],
) -> None:
    """Check that reference fields take defaults, init=False and KW_ONLY as a dataclass does."""
    assert str(inspect.signature(Rig)) == str(inspect.signature(PlainRig))
    with Motor.create(names("m")) as motor:
        with pytest.raises(TypeError, match="missing value"):
            Rig.create(names("r"), motor, tail=motor)
        with Rig.create(names("r"), motor, None, tail=motor) as rig:
            values = rig.snapshot()
            assert [values[n] for n in ("spare", "extra", "backup", "hidden")] == [
                None
            ] * 4
            assert rig.base.name == rig.tail.name == motor.name


def test_a_required_field_after_a_reference_default_is_refused_as_in_a_dataclass() -> (
    None
):
    """Check that a required field after a reference with a default is refused, as in a dataclass."""
    with pytest.raises(TypeError, match="without a default follows a field with one"):

        class Late(SharedBox):
            motor: Motor | None = None
            target: int  # type: ignore[misc]

    with pytest.raises(TypeError, match="non-default argument"):

        @dataclass
        class PlainLate:
            motor: Motor | None = None
            target: int  # type: ignore[misc]


def test_a_reference_without_none_is_never_empty(names: Callable[[str], str]) -> None:
    """Check that a reference annotated without None refuses None, as a default too."""

    class Mount(SharedBox):
        motor: Motor

    with (
        Motor.create(names("m")) as motor,
        Mount.create(names("mount"), motor) as mount,
    ):
        with pytest.raises(TypeError, match=r"Mount\.motor .* cannot be None"):
            mount.motor = None  # type: ignore[assignment]
        with pytest.raises(TypeError, match="cannot be None"):
            mount.update(motor=None)
        inner = mount.motor
        assert inner.name == motor.name
    with pytest.raises(TypeError, match="cannot be None"):

        class Unset(SharedBox):
            motor: Motor = None  # type: ignore[assignment]


def test_a_required_reference_and_an_optional_one_have_different_schemas(
    names: Callable[[str], str],
) -> None:
    """Check that a required reference and an optional one give different schema hashes."""

    class Firm(SharedBox, identity="sbtest/mount"):
        motor: Motor

    class Loose(SharedBox, identity="sbtest/mount"):
        motor: Motor | None

    with (
        Motor.create(names("m")) as motor,
        Firm.create(names("f"), motor) as firm,
        pytest.raises(SchemaMismatchError),
    ):
        Loose.attach(firm.name)


def test_a_default_box_is_checked_when_the_class_is_created(
    names: Callable[[str], str],
) -> None:
    """Check that a default box is checked when the class is defined, and a closed one raises."""
    with Motor.create(names("m"), 4) as existing, Other.create(names("o")) as other:

        class Preset(SharedBox):
            motor: Motor = field(default=existing)

        with Preset.create(names("p")) as preset:
            assert preset.motor.position == 4
        with pytest.raises(TypeError, match=r"Wrong\.motor expects a Motor box"):

            class Wrong(SharedBox):
                motor: Motor = field(default=other)

    with pytest.raises(BoxClosedError):

        class Stale(SharedBox):
            motor: Motor = field(default=existing)


def test_a_factory_box_is_checked_at_create(names: Callable[[str], str]) -> None:
    """Check that the box a default factory returns is checked at each creation."""
    made: list[SharedBox] = []

    def make() -> Motor:
        made.append(Other.create(names(f"o{len(made)}")))
        return made[-1]  # type: ignore[return-value]

    class Built(SharedBox):
        motor: Motor = field(default_factory=make)

    name = names("b")
    with pytest.raises(TypeError, match=r"Built\.motor expects a Motor box"):
        Built.create(name)
    with Motor.create(names("m")) as motor, Built.create(name, motor) as built:
        assert built.motor.name == motor.name
    for box in made:
        box.close()


class Wired(SharedBox):
    motor: Motor | None = None
    source: InitVar[str] = ""

    def __post_init__(self, source: str) -> None:
        if source:
            with Motor.attach(source) as motor:
                self.motor = motor


def test_post_init_can_assign_a_reference_before_the_box_is_published(
    names: Callable[[str], str],
) -> None:
    """Check that __post_init__ can assign a reference field before the box is published."""
    with (
        Motor.create(names("m"), 2) as motor,
        Wired.create(names("w"), source=motor.name) as wired,
    ):
        inner = wired.motor
        assert inner is not None
        assert (inner.name, inner.position) == (motor.name, 2)


def test_a_class_can_refer_to_itself(unique_name: str) -> None:
    """Check that a class whose field names the class itself can be created, with the field empty."""
    with Node.create(unique_name) as node, Link.create(f"{unique_name}-l") as link:
        assert node.link is None
        assert link.next is None


def test_a_reference_to_a_class_defined_later_is_refused() -> None:
    """Check that a reference to a class not defined yet raises TypeError naming the field."""
    with pytest.raises(
        TypeError,
        match=r"Early\.later: 'Later' is not defined; a class named in a field annotation, .* must be defined first",
    ):

        class Early(SharedBox):
            later: "Later | None" = None  # type: ignore[name-defined]  # noqa: F821


@pytest.mark.parametrize(
    "annotation",
    [
        "Optional[Later]",
        Optional["Later"],  # noqa: F821
        Union["Later", None],  # noqa: F821
    ],
    ids=["string", "optional", "union"],
)
def test_a_class_defined_later_is_named_alone_inside_another_annotation(
    annotation: Any,
) -> None:
    """Check that the error names only the undefined class when it is nested in Optional or Union."""
    with pytest.raises(
        TypeError,
        match=r"Early\.later: 'Later' is not defined; a class named in a field annotation, .* must be defined first",
    ):
        types.new_class(
            "Early",
            (SharedBox,),
            exec_body=lambda ns: ns.update(
                __annotations__={"later": annotation}, __module__=__name__
            ),
        )


@pytest.mark.skipif(
    sys.version_info < (3, 14), reason="annotations are evaluated lazily from 3.14"
)
def test_an_unquoted_class_defined_later_is_named_alone() -> None:
    """Check that an unquoted undefined class under lazy annotations is named alone in the error."""
    message = r"Early\.later: 'Later' is not defined; a class named in a field annotation, .* must be defined first"
    with pytest.raises(TypeError, match=message):

        class Early(SharedBox):
            later: Optional[Later] = None  # type: ignore[name-defined]  # noqa: F821, UP045

    with pytest.raises(TypeError, match=message):

        class Early(SharedBox):  # type: ignore[no-redef]
            later: Union[Later, None] = None  # type: ignore[name-defined]  # noqa: F821, UP007

    with pytest.raises(TypeError, match=message):

        class Early(SharedBox):  # type: ignore[no-redef]
            later: Later | None = None  # type: ignore[name-defined]  # noqa: F821


def test_a_default_box_of_a_field_naming_its_own_class_is_checked_at_class_creation(
    names: Callable[[str], str],
) -> None:
    """Check that a default box for a field naming its own class is checked against that class."""
    with Motor.create(names("m")) as motor:
        with pytest.raises(TypeError, match=r"Loop\.next expects a \S*Loop box"):

            class Loop(SharedBox):
                next: "Loop" = field(default=motor)

        # Before its own layout is set, Faster would be checked with Motor's.
        with pytest.raises(TypeError, match=r"Faster\.twin expects a \S*Faster box"):

            class Faster(Motor):
                twin: "Faster | None" = field(default=motor)


def test_assign_read_reassign_and_empty(names: Callable[[str], str]) -> None:
    """Check that a reference can be assigned, read, reassigned and emptied, and a read writes through."""
    with (
        Motor.create(names("a"), 1) as a,
        Motor.create(names("b"), 2) as b,
        Stage.create(names("s")) as stage,
    ):
        stage.motor = a
        first = stage.motor
        assert first is not None
        assert (first.name, first.position) == (a.name, 1)
        first.position = 10
        assert a.position == 10
        stage.motor = b
        second = stage.motor
        assert second is not None
        assert (second.name, second.position) == (b.name, 2)
        assert first.closed
        stage.motor = None
        assert stage.motor is None


def test_a_read_returns_the_box_it_attached_before(names: Callable[[str], str]) -> None:
    """Check that two reads of an unchanged reference return the same box object."""
    with Motor.create(names("m")) as motor, Stage.create(names("s"), 0, motor) as stage:
        assert stage.motor is stage.motor


def test_a_reassignment_through_another_handle_is_seen_on_the_next_read(
    names: Callable[[str], str],
) -> None:
    """Check that a reassignment through another handle is seen on the next read, which closes the old box."""
    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), motor=a) as stage,
        Stage.attach(stage.name) as other,
    ):
        old = stage.motor
        other.motor = b
        new = stage.motor
        assert new is not None
        assert new.name == b.name
        assert old is not None
        assert old.closed


def test_a_box_that_was_removed_is_a_broken_reference(
    names: Callable[[str], str],
) -> None:
    """Check that reading a reference to a removed box raises BrokenReferenceError."""
    with Stage.create(names("s")) as stage:
        motor = Motor.create(names("m"))
        stage.motor = motor
        motor.close()
        Motor.unlink(motor.name)
        with pytest.raises(
            BrokenReferenceError,
            match=rf"Stage\.motor refers to box '{motor.name}', which no longer exists",
        ):
            _ = stage.motor


# A box class as a parameter would stay referenced by pytest until after the extension is
# freed at exit, which nanobind reports as leaked instances.
@pytest.mark.parametrize("same_class", [True, False])
def test_a_box_created_again_under_its_name_is_a_broken_reference(
    names: Callable[[str], str], same_class: bool
) -> None:
    """Check that a box created again under the referred name raises BrokenReferenceError on read."""
    again: type[SharedBox] = Motor if same_class else Other
    with Stage.create(names("s")) as stage:
        motor = Motor.create(names("m"))
        stage.motor = motor
        motor.close()
        Motor.unlink(motor.name)
        with (
            again.create(motor.name),
            pytest.raises(
                BrokenReferenceError,
                match=rf"Stage\.motor refers to box '{motor.name}', "
                "which was created again after it was assigned",
            ),
        ):
            _ = stage.motor


def test_a_box_of_other_fields_is_refused_and_nothing_is_written(
    names: Callable[[str], str],
) -> None:
    """Check that assigning a box of another schema or a non-box raises TypeError and writes nothing."""
    with (
        Motor.create(names("m")) as motor,
        Other.create(names("o")) as other,
        Stage.create(names("s"), 0, motor) as stage,
    ):
        with pytest.raises(
            TypeError,
            match="Stage.motor expects a Motor box, one of a subclass, or None, got Other",
        ):
            stage.motor = other  # type: ignore[assignment]
        with pytest.raises(TypeError, match="got int"):
            stage.motor = 3  # type: ignore[assignment]
        inner = stage.motor
        assert inner is not None
        assert inner.name == motor.name


def test_a_box_of_another_class_with_the_same_schema_is_accepted(
    names: Callable[[str], str],
) -> None:
    """Check that a box of another class with the same schema hash is accepted and read with the first class."""
    with MotorCopy.create(names("c"), 4) as copy, Stage.create(names("s")) as stage:
        stage.motor = copy  # type: ignore[assignment]
        inner = stage.motor
        # Motor was defined first, so its class reads a box of either.
        assert type(inner) is Motor
        assert inner.position == 4


def test_assigning_a_closed_box_raises(names: Callable[[str], str]) -> None:
    """Check that assigning a closed box raises BoxClosedError."""
    motor = Motor.create(names("m"))
    motor.close()
    with Stage.create(names("s")) as stage, pytest.raises(BoxClosedError):
        stage.motor = motor


def test_update_writes_references_and_scalars_together(
    names: Callable[[str], str],
) -> None:
    """Check that update() writes references and scalars together, and writes nothing if one is refused."""
    with Motor.create(names("m")) as motor, Stage.create(names("s")) as stage:
        stage.update(target=5, motor=motor)
        inner = stage.motor
        assert (stage.target, inner and inner.name) == (5, motor.name)
        with pytest.raises(TypeError):
            stage.update(target=6, motor=3)
        assert stage.target == 5


def test_a_reference_is_stored_as_create_id_schema_hash_and_name(
    names: Callable[[str], str],
) -> None:
    """Check that a reference is stored as create id, schema hash and name, and None as zero bytes."""
    index = Stage.__layout__.by_name["motor"].index
    with Motor.create(names("m")) as motor, Stage.create(names("s"), 0, motor) as stage:
        assert stage._segment._read(index) == struct.pack(
            "<QQ240s",
            motor._segment.create_id,
            Motor.__layout__.schema_hash,
            motor.name.encode(),
        )
        stage.motor = None
        assert stage._segment._read(index) == bytes(256)


def test_close_closes_the_boxes_a_read_attached(names: Callable[[str], str]) -> None:
    """Check that close() closes the boxes reads attached and leaves the caller's own box open."""
    with Motor.create(names("m")) as motor:
        stage = Stage.create(names("s"), 0, motor)
        inner = stage.motor
        assert inner is not None
        stage.close()
        assert inner.closed
        assert not motor.closed


def test_a_read_attaches_again_after_the_caller_closed_the_box(
    names: Callable[[str], str],
) -> None:
    """Check that a read attaches the box again after the caller closed the one it returned."""
    with Motor.create(names("m")) as motor, Stage.create(names("s"), 0, motor) as stage:
        first = stage.motor
        assert first is not None
        first.close()
        again = stage.motor
        assert again is not None
        assert again is not first
        assert not again.closed


def test_a_box_can_refer_to_itself(names: Callable[[str], str]) -> None:
    """Check that a box can refer to itself and read itself back."""
    with Node.create(names("n"), 1) as node, Link.create(names("l"), 2) as link:
        node.link = node
        link.next = link
        inner = node.link
        assert inner is not None
        assert (inner.name, inner.value) == (node.name, 1)
        nxt = link.next
        assert nxt is not None
        assert nxt.name == link.name


def test_a_pickled_box_whose_inner_box_was_removed_unpickles_and_raises_on_read(
    names: Callable[[str], str],
) -> None:
    """Check that a pickled box unpickles after its referred box was removed and raises only on read."""
    with Stage.create(names("s")) as stage:
        motor = Motor.create(names("m"))
        stage.motor = motor
        data = pickle.dumps(stage)
        motor.close()
        Motor.unlink(motor.name)
        with (
            pickle.loads(data) as copy,
            pytest.raises(BrokenReferenceError, match="no longer exists"),
        ):
            _ = copy.motor


def test_a_reference_field_cannot_be_deleted(names: Callable[[str], str]) -> None:
    """Check that deleting a reference field raises AttributeError."""
    with Stage.create(names("s")) as stage, pytest.raises(AttributeError):
        del stage.motor


def test_threads_reading_while_the_reference_moves_leave_no_box_open(
    names: Callable[[str], str],
) -> None:
    """Check that threads reading while the reference moves leave every returned box closed at the end."""
    returned: dict[int, Motor] = {}
    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        stop = threading.Event()

        def read() -> None:
            while not stop.is_set():
                inner = stage.motor
                if inner is not None:
                    returned[id(inner)] = inner

        threads = [threading.Thread(target=read) for _ in range(4)]
        for thread in threads:
            thread.start()
        for i in range(300):
            stage.motor = b if i % 2 else a
        stop.set()
        for thread in threads:
            thread.join()
    assert returned
    assert {inner.name for inner in returned.values()} <= {a.name, b.name}
    assert all(inner.closed for inner in returned.values())


def test_a_class_that_refers_to_itself_is_freed_at_exit() -> None:
    """Check that a module with a self-referring class exits without leak reports."""
    result = subprocess.run(
        [sys.executable, "-c", "import refs_future"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "leaked" not in result.stderr


def test_a_reference_of_a_closed_box_raises_even_with_a_kept_handle(
    names: Callable[[str], str],
) -> None:
    """Check that reading a reference of a closed box raises BoxClosedError even with a cached handle."""
    with Motor.create(names("m")) as motor:
        stage = Stage.create(names("s"), 0, motor)
        assert stage.motor is not None
        stage.close()
        with pytest.raises(BoxClosedError):
            _ = stage.motor


def test_a_box_of_a_subclass_is_read_back_as_the_subclass(
    names: Callable[[str], str],
) -> None:
    """Check that a box of a subclass is read back as that subclass."""
    with FastMotor.create(names("f"), 6) as fast, Stage.create(names("s")) as stage:
        stage.motor = fast
        inner = stage.motor
        assert type(inner) is FastMotor
        assert inner.position == 6


def test_a_handle_keeps_the_box_it_read_after_the_box_is_removed(
    names: Callable[[str], str],
) -> None:
    """Check that a handle keeps the box it read after removal, while a new handle sees it as broken on Linux."""
    with Stage.create(names("s")) as stage:
        motor = Motor.create(names("m"), 3)
        stage.motor = motor
        kept = stage.motor
        motor.close()
        Motor.unlink(motor.name)
        assert stage.motor is kept
        assert kept is not None
        assert kept.position == 3
        with Stage.attach(stage.name) as other:
            if sys.platform == "win32":
                # The kept handle holds the segment, and so its name, in existence.
                inner = other.motor
                assert inner is not None
                assert inner.position == 3
            else:
                with pytest.raises(BrokenReferenceError, match="no longer exists"):
                    _ = other.motor


def assign_a_class_only_this_process_defines(stage: Stage, name: str) -> None:
    class Secret(Motor):
        pass

    with stage, Secret.create(name) as secret:
        stage.motor = secret
    Secret.unlink(name)


def test_a_box_of_a_class_this_process_never_defined_raises_unknown_class(
    names: Callable[[str], str],
) -> None:
    """Check that a reference to a box of a class this process never defined raises UnknownBoxClassError."""
    ctx = mp.get_context("spawn")
    with Stage.create(names("s")) as stage:
        secret_name = names("secret")
        child = ctx.Process(
            target=assign_a_class_only_this_process_defines, args=(stage, secret_name)
        )
        child.start()
        child.join(20)
        assert child.exitcode == 0
        with pytest.raises(
            UnknownBoxClassError,
            match=rf"Stage\.motor refers to box '{secret_name}', whose class \(schema "
            r"hash 0x[0-9a-f]{16}\) is not defined in this process; import",
        ):
            _ = stage.motor


def test_snapshot_shows_a_reference_as_a_box_ref(names: Callable[[str], str]) -> None:
    """Check that snapshot() and repr show a reference as a BoxRef and an empty one as None."""
    with Motor.create(names("m")) as motor, Stage.create(names("s"), 3, motor) as stage:
        # The create id is not public; the test needs it to build the BoxRef a snapshot reports.
        create_id = motor._segment.create_id
        assert stage.snapshot() == {
            "target": 3,
            "motor": BoxRef(motor.name, Motor.__layout__.schema_hash, create_id),
        }
        assert "motor=BoxRef(" in repr(stage)
        stage.motor = None
        assert stage.snapshot() == {"target": 3, "motor": None}


def test_snapshot_follow_nests_the_boxes_referred_to(
    names: Callable[[str], str],
) -> None:
    """Check that follow=True nests each referred box's snapshot and takes follow only by keyword."""
    with (
        Motor.create(names("m"), 7) as motor,
        Stage.create(names("s"), 3, motor) as stage,
    ):
        assert stage.snapshot(follow=True) == {
            "target": 3,
            "motor": {"position": 7},
        }
        assert motor.snapshot(follow=True) == {"position": 7}
        with pytest.raises(TypeError):
            stage.snapshot(True)  # type: ignore[call-arg]


def test_snapshot_follow_ends_at_a_box_it_already_read(
    names: Callable[[str], str],
) -> None:
    """Check that follow=True leaves a box it already read as a BoxRef, so loops and repeats end."""
    with (
        Node.create(names("a"), 1) as a,
        Node.create(names("b"), 2) as b,
        Motor.create(names("m"), 5) as motor,
        Pair.create(names("p"), motor, motor) as pair,
    ):
        assert pair.snapshot(follow=True) == {
            "first": {"position": 5},
            "second": pair.snapshot()["second"],
        }
        a.link = b
        b.link = a
        a_ref = b.snapshot()["link"]
        assert a.snapshot(follow=True) == {
            "value": 1,
            "link": {"value": 2, "link": a_ref},
        }
        a.link = a
        assert a.snapshot(follow=True) == {"value": 1, "link": a_ref}


def test_snapshot_follow_reads_a_chain_longer_than_the_recursion_limit(
    names: Callable[[str], str],
) -> None:
    """Check that follow=True nests a chain of 2000 boxes and that closing the chain afterwards succeeds."""
    length = 2000
    with contextlib.ExitStack() as stack:
        if sys.platform.startswith("linux"):
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            # Every box holds a descriptor, and so does the handle its parent attaches to follow it.
            needed = 2 * length + 256
            if hard != resource.RLIM_INFINITY and hard < needed:
                pytest.skip(f"needs {needed} open files, the hard limit is {hard}")
            if soft != resource.RLIM_INFINITY and soft < needed:
                resource.setrlimit(resource.RLIMIT_NOFILE, (needed, hard))
                stack.callback(resource.setrlimit, resource.RLIMIT_NOFILE, (soft, hard))
        head = None
        for value in reversed(range(length)):
            head = stack.enter_context(Node.create(names(str(value)), value, head))
        assert head is not None
        level: Any = head.snapshot(follow=True)
        for value in range(length):
            assert level["value"] == value
            level = level["link"]
        assert level is None


def test_snapshot_follow_raises_on_a_broken_reference(
    names: Callable[[str], str],
) -> None:
    """Check that snapshot() leaves a broken reference alone and follow=True raises on it."""
    with Stage.create(names("s")) as stage:
        motor = Motor.create(names("m"))
        stage.motor = motor
        motor.close()
        Motor.unlink(motor.name)
        assert stage.snapshot()["motor"] is not None
        with pytest.raises(BrokenReferenceError, match="no longer exists"):
            stage.snapshot(follow=True)


def test_events_and_watch_report_reassignments_as_box_refs(
    names: Callable[[str], str],
) -> None:
    """Check that events and watch() report a reassigned or emptied reference as BoxRef or None."""
    seen: queue.Queue[tuple[object, object]] = queue.Queue()
    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        # The create id is not public; the test needs it to build the BoxRef a snapshot reports.
        a_ref = BoxRef(a.name, Motor.__layout__.schema_hash, a._segment.create_id)
        b_ref = BoxRef(b.name, Motor.__layout__.schema_hash, b._segment.create_id)
        stage.events.motor.connect(lambda new, old: seen.put((new, old)))
        watched = iter(stage.watch("motor"))
        stage.motor = b
        assert seen.get(timeout=5) == (b_ref, a_ref)
        assert next(watched) == b_ref
        stage.motor = None
        assert seen.get(timeout=5) == (None, b_ref)
        assert next(watched) is None


def test_box_class_is_the_class_this_process_has_for_the_hash(
    names: Callable[[str], str],
) -> None:
    """Check that box_class and equality follow the box's own class and create id."""
    with FastMotor.create(names("f")) as fast, Stage.create(names("s")) as stage:
        stage.motor = fast
        ref = stage.snapshot()["motor"]
        assert ref is not None
        assert ref.box_class is FastMotor
    assert BoxRef("m", Motor.__layout__.schema_hash, 1).box_class is Motor
    assert BoxRef("m", 0x5EED, 1).box_class is None
    assert BoxRef("m", 0x5EED, 1) != BoxRef("m", 0x5EED, 2)


def point_stage_at(stage: Stage, motor_name: str) -> None:
    with stage, Motor.attach(motor_name) as motor:
        before = stage.motor
        assert before is not None
        before.position = 1
        stage.motor = motor


def test_a_reassignment_in_another_process_is_seen_on_the_next_read(
    names: Callable[[str], str],
) -> None:
    """Check that a child given the pickled box writes through its reference and reassigns it."""
    ctx = mp.get_context("spawn")
    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b"), 5) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        old = stage.motor
        child = ctx.Process(target=point_stage_at, args=(stage, b.name))
        child.start()
        child.join(20)
        assert child.exitcode == 0
        assert a.position == 1
        new = stage.motor
        assert new is not None
        assert (new.name, new.position) == (b.name, 5)
        assert old is not None
        assert old.closed


def define_a_motor_class_with_the_identity_of(identity: str) -> type[SharedBox]:
    class Gone(SharedBox, identity=identity):
        position: int = 0

    return Gone


def test_a_later_class_with_the_same_hash_reads_after_the_first_is_freed(
    names: Callable[[str], str],
) -> None:
    """Check that freeing the first class with a schema hash leaves a later one to read boxes."""
    gone = weakref.ref(define_a_motor_class_with_the_identity_of("sbtest/kept"))

    class Kept(SharedBox, identity="sbtest/kept"):
        position: int = 0

    class Mount(SharedBox):
        motor: Kept | None = None

    gc.collect()
    assert gone() is None
    with Kept.create(names("k"), 8) as kept, Mount.create(names("m"), kept) as mount:
        inner = mount.motor
        assert type(inner) is Kept
        assert inner.position == 8


@pytest.mark.parametrize("name", [b"", b"bad/name"], ids=["empty", "invalid"])
def test_a_stored_reference_with_an_invalid_name_is_a_broken_reference(
    names: Callable[[str], str], name: bytes
) -> None:
    """Check that a reference another writer stored with an invalid name raises BrokenReferenceError."""
    index = Stage.__layout__.by_name["motor"].index
    raw = struct.pack("<QQ240s", 7, Motor.__layout__.schema_hash, name)
    with Stage.create(names("s")) as stage:
        stage._segment._write([(index, raw)])
        with pytest.raises(
            BrokenReferenceError,
            match=r"Stage\.motor refers to box .*not a valid box name",
        ):
            _ = stage.motor


def test_update_through_an_override_still_converts_references(
    names: Callable[[str], str],
) -> None:
    """Check that super().update from an override stores a box in a reference field."""

    class Holder(SharedBox):
        target: Motor | None = None

        def update(self, **values: Any) -> None:
            super().update(**values)

    with Motor.create(names("m")) as motor, Holder.create(names("h")) as holder:
        holder.update(target=motor)
        assert holder.target is not None
        assert holder.target.name == motor.name


def test_update_of_a_reference_keeps_no_objects(names: Callable[[str], str]) -> None:
    """Check that repeated update of a reference field leaves no objects behind."""
    with Motor.create(names("m")) as motor, Holder.create(names("h"), None) as holder:
        holder.update(motor=motor)
        gc.collect()
        before = sys.getallocatedblocks()
        for _ in range(1000):
            holder.update(motor=motor)
        gc.collect()
        # Each update converts the box to a new tuple of two ints and a name; one kept
        # per update would add thousands of blocks, while 100 covers allocator noise.
        assert sys.getallocatedblocks() <= before + 100


def test_snapshot_following_a_reference_keeps_no_objects(
    names: Callable[[str], str],
) -> None:
    """Check that repeated snapshot(follow=True) of a box with a reference field leaves no objects behind."""
    with Motor.create(names("m")) as motor, Holder.create(names("h"), motor) as holder:
        holder.snapshot(follow=True)
        gc.collect()
        before = sys.getallocatedblocks()
        for _ in range(1000):
            holder.snapshot(follow=True)
        gc.collect()
        # Each snapshot makes a BoxRef and a nested snapshot; one kept per snapshot
        # would add thousands of blocks, while 100 covers allocator noise.
        assert sys.getallocatedblocks() <= before + 100


def test_a_reference_holds_a_name_of_240_characters(unique_name: str) -> None:
    """Check that a reference field stores and follows a box whose name has 240 characters."""
    name = (unique_name + ":" + "t" * 240)[:240]
    with (
        Motor.create(name) as motor,
        Stage.create(f"{unique_name}-s", motor=motor) as stage,
    ):
        assert stage.motor is not None
        assert stage.motor.name == name
    Motor.unlink(name)
    Stage.unlink(f"{unique_name}-s")
