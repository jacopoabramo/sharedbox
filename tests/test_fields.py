import dataclasses
import inspect
import multiprocessing as mp
import os
import pickle
import sys
import time
import types
from dataclasses import KW_ONLY, MISSING, InitVar, dataclass
from multiprocessing.synchronize import Event
from typing import Annotated, Any, ClassVar, cast

import pytest

from sharedbox import (
    Capacity,
    SegmentExistsError,
    SegmentNotFoundError,
    SharedBox,
    field,
    fields,
)


class Stage(SharedBox):
    x: float
    label: Annotated[str, Capacity(8)] = field(default="s", repr=False)
    speed: float = field(default_factory=lambda: 1.5)
    moves: int = field(default=0, init=False)
    limit: int = field(
        default=9, kw_only=True, metadata={"unit": "mm"}, doc="Upper bound."
    )
    _: KW_ONLY
    y: float = 0.0
    z: float = field(default=0.0, kw_only=False)


@dataclass
class PlainStage:
    x: float
    label: Annotated[str, Capacity(8)] = dataclasses.field(default="s", repr=False)
    speed: float = dataclasses.field(default_factory=lambda: 1.5)
    moves: int = dataclasses.field(default=0, init=False)
    limit: int = dataclasses.field(default=9, kw_only=True, metadata={"unit": "mm"})
    _: KW_ONLY
    y: float = 0.0
    z: float = dataclasses.field(default=0.0, kw_only=False)


def post_init(self: object, *initvars: object) -> None:
    pass


def box_twin(
    annotations: dict[str, Any], options: dict[str, dict[str, Any]], kw_only: bool
) -> type[SharedBox]:
    def body(namespace: dict[str, Any]) -> None:
        namespace["__annotations__"] = dict(annotations)
        namespace.update({name: field(**kw) for name, kw in options.items()})
        namespace["__post_init__"] = post_init

    return cast(
        type[SharedBox],
        types.new_class("Twin", (SharedBox,), {"kw_only": kw_only}, body),
    )


def dataclass_twin(
    annotations: dict[str, Any], options: dict[str, dict[str, Any]], kw_only: bool
) -> type:
    namespace: dict[str, Any] = {
        "__annotations__": dict(annotations),
        "__post_init__": post_init,
        **{name: dataclasses.field(**kw) for name, kw in options.items()},
    }
    return dataclass(kw_only=kw_only)(type("Twin", (), namespace))


ACCEPTED = [
    pytest.param(
        {"a": int, "b": int},
        {"a": {"default": 0, "kw_only": True}},
        False,
        id="kw_only default before a required field",
    ),
    pytest.param(
        {"a": int, "b": int},
        {"a": {"default": 0, "init": False}},
        False,
        id="init=False default before a required field",
    ),
    pytest.param(
        {"a": int, "b": int},
        {"b": {"kw_only": False}},
        True,
        id="kw_only=False in a kw_only class",
    ),
    pytest.param(
        {"a": int, "_": KW_ONLY, "b": int},
        {"b": {"default": 1, "kw_only": False}},
        False,
        id="kw_only=False after KW_ONLY",
    ),
    pytest.param(
        {"a": int, "b": int},
        {"b": {"default_factory": int}},
        False,
        id="factory default",
    ),
]
REFUSED = [
    pytest.param(
        {"a": int, "b": int},
        {"a": {"default": 0}},
        False,
        id="required after field default",
    ),
    pytest.param(
        {"a": int, "b": int},
        {"a": {"default_factory": int}},
        False,
        id="required after factory default",
    ),
    pytest.param(
        {"a": int, "_": KW_ONLY, "b": int},
        {"a": {"default": 0}, "b": {"kw_only": False}},
        False,
        id="kw_only=False required after a default",
    ),
    pytest.param(
        {"a": int, "b": int},
        {"a": {"default": 0, "kw_only": False}, "b": {"kw_only": False}},
        True,
        id="kw_only=False fields in a kw_only class",
    ),
]


@pytest.mark.parametrize(("annotations", "options", "kw_only"), ACCEPTED)
def test_constructor_signature_matches_a_dataclass(
    annotations: dict[str, Any], options: dict[str, dict[str, Any]], kw_only: bool
) -> None:
    box = box_twin(annotations, options, kw_only)
    plain = dataclass_twin(annotations, options, kw_only)
    assert str(inspect.signature(box)) == str(inspect.signature(plain))


@pytest.mark.parametrize(("annotations", "options", "kw_only"), REFUSED)
def test_field_order_is_refused_where_a_dataclass_refuses_it(
    annotations: dict[str, Any], options: dict[str, dict[str, Any]], kw_only: bool
) -> None:
    with pytest.raises(TypeError):
        dataclass_twin(annotations, options, kw_only)
    with pytest.raises(TypeError, match="without a default follows"):
        box_twin(annotations, options, kw_only)


def test_field_options_behave_as_in_a_dataclass(unique_name: str) -> None:
    assert str(inspect.signature(Stage)) == str(inspect.signature(PlainStage))
    plain = PlainStage(1.0, "t", z=2.0, limit=3)
    with Stage.create(unique_name, 1.0, "t", z=2.0, limit=3) as box:
        assert box.snapshot() == dataclasses.asdict(plain)
        assert repr(box) == repr(plain).replace("PlainStage", "Stage", 1)
    assert [(f.name, f.init, f.repr, f.kw_only) for f in fields(Stage)] == [
        (f.name, f.init, f.repr, f.kw_only) for f in dataclasses.fields(PlainStage)
    ]


def test_an_init_false_field_takes_no_value(unique_name: str) -> None:
    with pytest.raises(TypeError):
        PlainStage(1.0, moves=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="moves"):
        Stage.create(unique_name, 1.0, moves=1)
    with Stage.create(unique_name, 1.0) as box:
        box.moves = 4
        assert box.moves == 4


def test_init_false_needs_a_default() -> None:
    with pytest.raises(TypeError, match="init=False"):

        class Bad(SharedBox):
            count: int = field(init=False)


def test_default_and_factory_together_are_refused() -> None:
    with pytest.raises(ValueError, match="both"):
        field(default=0, default_factory=int)


@pytest.mark.parametrize("option", ["compare", "hash"])
def test_field_has_no_compare_or_hash(option: str) -> None:
    options: dict[str, Any] = {option: False}
    with pytest.raises(TypeError, match=option):
        field(**options)


def test_a_bad_default_fails_at_class_definition() -> None:
    with pytest.raises(ValueError):

        class Bad(SharedBox):
            label: Annotated[str, Capacity(2)] = field(default="long")


def test_factory_runs_once_per_create_and_never_on_attach(unique_name: str) -> None:
    calls: list[int] = []

    def make() -> int:
        calls.append(1)
        return len(calls)

    class Counted(SharedBox):
        value: int = field(default_factory=make)

    with Counted.create(unique_name) as box, Counted.attach(unique_name) as other:
        assert (box.value, other.value) == (1, 1)
    with Counted.create(f"{unique_name}-2", 7) as given:
        assert given.value == 7
    Counted.unlink(f"{unique_name}-2")
    assert calls == [1]


def test_a_factory_value_that_does_not_fit_is_refused_at_create(
    unique_name: str,
) -> None:
    class Labelled(SharedBox):
        label: Annotated[str, Capacity(4)] = field(default_factory=lambda: "too long")

    with pytest.raises(ValueError, match="Labelled.label"):
        Labelled.create(unique_name)
    with Labelled.create(unique_name, "ok") as box:
        assert box.label == "ok"


def test_fields_describe_a_class_and_its_boxes(unique_name: str) -> None:
    described = fields(Stage)
    assert [f.name for f in described] == [
        "x",
        "label",
        "speed",
        "moves",
        "limit",
        "y",
        "z",
    ]
    x, _, speed, _, limit, *_ = described
    assert (x.default, x.default_factory, x.type) == (MISSING, MISSING, float)
    assert speed.default_factory() == 1.5
    assert (limit.default, limit.metadata, limit.doc) == (
        9,
        {"unit": "mm"},
        "Upper bound.",
    )
    with Stage.create(unique_name, 1.0) as box:
        assert fields(box) == described
    with pytest.raises(TypeError):
        limit.metadata["unit"] = "m"  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        limit.default = 1  # type: ignore[misc]
    with pytest.raises(TypeError):
        fields(SharedBox)
    with pytest.raises(TypeError):
        fields(PlainStage)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "namespace",
    [
        {
            "__annotations__": {"value": int, "_hidden": int},
            "_hidden": field(default=0),
        },
        {
            "__annotations__": {"value": int, "shared": ClassVar[int]},
            "shared": field(default=0),
        },
    ],
    ids=["underscore name", "ClassVar"],
)
def test_field_on_a_name_that_is_not_a_field_is_refused(
    namespace: dict[str, Any],
) -> None:
    with pytest.raises(TypeError, match="not fields"):
        types.new_class("Stray", (SharedBox,), {}, lambda ns: ns.update(namespace))


class Described(SharedBox):
    label: Annotated[str, Capacity(8)] = field(
        default="b", repr=False, metadata={"k": 1}
    )
    count: int = field(default_factory=lambda: 7)


class DescribedChild(Described, kw_only=True):
    extra: int = 0


def test_a_subclass_keeps_its_bases_field_options(unique_name: str) -> None:
    with DescribedChild.create(unique_name, extra=1) as box:
        assert repr(box) == "DescribedChild(count=7, extra=1)"
    assert fields(DescribedChild)[0].metadata == {"k": 1}
    assert [f.kw_only for f in fields(DescribedChild)] == [False, False, True]
    # A dataclass ignores such an attribute; a box cannot, as it would hide the descriptor.
    with pytest.raises(
        TypeError,
        match="Overridden.count overrides the field inherited from Described",
    ):

        class Overridden(Described):
            count = 3


class Hidden(SharedBox):
    x: int = 0
    a: int = field(default=1, init=False, repr=False, metadata={"k": 1})


class Redeclared(Hidden):
    a: int = 5


@dataclass
class PlainHidden:
    x: int = 0
    a: int = dataclasses.field(default=1, init=False, repr=False, metadata={"k": 1})


@dataclass
class PlainRedeclared(PlainHidden):
    a: int = 5


def test_an_annotated_override_drops_the_bases_field_options() -> None:
    box, plain = fields(Redeclared)[1], dataclasses.fields(PlainRedeclared)[1]
    assert (box.default, box.init, box.repr, box.metadata) == (
        plain.default,
        plain.init,
        plain.repr,
        plain.metadata,
    )
    assert str(inspect.signature(Redeclared)) == str(inspect.signature(PlainRedeclared))


class KwBase(SharedBox, kw_only=True):
    a: int


class PositionalChild(KwBase):
    b: int


class PositionalBase(SharedBox):
    a: int = 0


class KwChild(PositionalBase, kw_only=True):
    b: int


class MarkerBase(SharedBox):
    a: int
    _: KW_ONLY
    b: int = 0


class MarkerChild(MarkerBase):
    c: int = 0


@dataclass(kw_only=True)
class PlainKwBase:
    a: int


@dataclass
class PlainPositionalChild(PlainKwBase):
    b: int


@dataclass
class PlainPositionalBase:
    a: int = 0


@dataclass(kw_only=True)
class PlainKwChild(PlainPositionalBase):
    b: int


@dataclass
class PlainMarkerBase:
    a: int
    _: KW_ONLY
    b: int = 0


@dataclass
class PlainMarkerChild(PlainMarkerBase):
    c: int = 0


@pytest.mark.parametrize(
    "name",
    ["PositionalChild", "KwChild", "MarkerChild"],
    ids=["kw_only base", "kw_only child", "KW_ONLY in the base"],
)
def test_kw_only_is_inherited_as_in_a_dataclass(name: str) -> None:
    # Classes held by parametrize outlive the extension at exit, which nanobind reports as leaks.
    box, plain = globals()[name], globals()[f"Plain{name}"]
    assert str(inspect.signature(box)) == str(inspect.signature(plain))


@pytest.mark.parametrize("name", ["label", "extra"], ids=["inherited", "new name"])
def test_an_unannotated_field_call_is_refused_as_in_a_dataclass(name: str) -> None:
    with pytest.raises(
        TypeError, match=f"'{name}' is a field but has no type annotation"
    ):
        dataclass(type("Plain", (PlainStage,), {name: dataclasses.field(default="x")}))
    with pytest.raises(
        TypeError, match=f"Loose: '{name}' is a field but has no type annotation"
    ):
        type("Loose", (Stage,), {name: field(default="x")})


class Offsets(SharedBox, identity="sbtest/offsets"):
    position: int
    first: InitVar[int]
    second: InitVar[int] = 10
    total: int = 0

    def __post_init__(self, first: int, second: int) -> None:
        self.total = first * 100 + second


class Stored(SharedBox, identity="sbtest/offsets"):
    position: int
    total: int = 0


class Shifted(Offsets):
    extra: int = 0


def test_initvars_reach_post_init_in_order_and_are_not_stored(
    unique_name: str,
) -> None:
    with Offsets.create(unique_name, 5, 1, 2) as box:
        assert box.snapshot() == {"position": 5, "total": 102}
        assert list(box.events) == ["position", "total"]
        assert not hasattr(box, "second")
        with Stored.attach(unique_name) as stored:
            assert stored.total == 102
    with Offsets.create(f"{unique_name}-2", 5, first=3) as box:
        assert box.total == 310
    Offsets.unlink(f"{unique_name}-2")
    assert [f.name for f in fields(Offsets)] == ["position", "total"]
    with Shifted.create(f"{unique_name}-3", 1, 2, extra=4) as child:
        assert (child.total, child.extra) == (210, 4)
    Shifted.unlink(f"{unique_name}-3")


def test_initvar_signature_and_order_match_a_dataclass() -> None:
    annotations = {"a": int, "offset": InitVar[int], "_": KW_ONLY, "b": int}
    options = {"offset": {"default": 0}, "b": {"default": 1}}
    assert str(inspect.signature(box_twin(annotations, options, False))) == str(
        inspect.signature(dataclass_twin(annotations, options, False))
    )
    refused = {"a": int, "offset": InitVar[int]}
    with pytest.raises(TypeError):
        dataclass_twin(refused, {"a": {"default": 0}}, False)
    with pytest.raises(TypeError, match="without a default follows"):
        box_twin(refused, {"a": {"default": 0}}, False)


def test_initvar_needs_post_init() -> None:
    with pytest.raises(TypeError, match="__post_init__"):

        class Bad(SharedBox):
            value: int = 0
            offset: InitVar[int] = 0


def test_initvar_takes_no_factory() -> None:
    with pytest.raises(TypeError, match="default_factory"):

        class Bad(SharedBox):
            value: int = 0
            offset: InitVar[int] = field(default_factory=int)

            def __post_init__(self, offset: int) -> None:
                pass


class Runs(SharedBox):
    count: int = 0

    def __post_init__(self) -> None:
        self.count += 1


def test_post_init_runs_only_when_a_box_is_created(unique_name: str) -> None:
    with Runs.create(unique_name) as box, Runs.attach(unique_name) as other:
        copy = pickle.loads(pickle.dumps(box))
        assert (box.count, other.count, copy.count) == (1, 1, 1)
        copy.close()


# Keeps the box a raising __post_init__ saw alive, as user code may, so garbage
# collection cannot free its segment for it.
FAILED_BOXES: list[SharedBox] = []


class Failing(SharedBox):
    value: int = 0
    error: InitVar[type[BaseException] | None] = None

    def __post_init__(self, error: type[BaseException] | None) -> None:
        self.value = 5
        if error is not None:
            FAILED_BOXES.append(self)
            raise error("from __post_init__")


class Stop(BaseException):
    pass


@pytest.mark.parametrize("error", [RuntimeError, Stop])
def test_a_raising_post_init_leaves_no_box(
    unique_name: str, error: type[BaseException]
) -> None:
    with pytest.raises(error, match="from __post_init__"):
        Failing.create(unique_name, error=error)
    assert FAILED_BOXES.pop().closed
    with pytest.raises(SegmentNotFoundError):
        Failing.attach(unique_name)
    with Failing.create(unique_name) as box:
        assert box.value == 5


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="/dev/shm exists on Linux only"
)
def test_a_raising_post_init_leaves_no_file_in_dev_shm(unique_name: str) -> None:
    with pytest.raises(RuntimeError):
        Failing.create(unique_name, error=RuntimeError)
    FAILED_BOXES.clear()
    assert not os.path.exists(f"/dev/shm/sharedbox.{unique_name}")


class Probe(SharedBox, lock_timeout=0.2):
    value: int = 0

    def __post_init__(self) -> None:
        with pytest.raises(SegmentExistsError):
            Probe.create(self.name)
        with pytest.raises(SegmentNotFoundError):
            Probe.attach(self.name)


def test_the_name_is_taken_but_not_attachable_during_post_init(
    unique_name: str,
) -> None:
    with Probe.create(unique_name), Probe.attach(unique_name) as other:
        assert other.value == 0


class Slow(SharedBox):
    value: int = 0
    started: InitVar[Event | None] = None

    def __post_init__(self, started: Event | None) -> None:
        if started is not None:
            started.set()
            time.sleep(0.5)
        self.value = 42


def attach_while_post_init_runs(
    name: str, started: Event, results: "mp.Queue[int]"
) -> None:
    started.wait(20)
    with Slow.attach(name) as box:
        results.put(box.value)


def test_a_process_attaching_during_post_init_sees_its_writes(
    unique_name: str,
) -> None:
    ctx = mp.get_context("spawn")
    started = ctx.Event()
    results: mp.Queue[int] = ctx.Queue()
    child = ctx.Process(
        target=attach_while_post_init_runs, args=(unique_name, started, results)
    )
    child.start()
    with Slow.create(unique_name, started=started):
        assert results.get(timeout=20) == 42
        child.join(20)
    assert child.exitcode == 0


class Exported(SharedBox):
    value: int = 0

    def __post_init__(self) -> None:
        with pytest.raises(BufferError, match="not published yet"):
            self.__sharedbox_box__()


def test_a_box_goes_to_other_extensions_only_after_post_init(
    unique_name: str,
) -> None:
    with Exported.create(unique_name) as box:
        assert type(box.__sharedbox_box__()).__name__ == "PyCapsule"


class Held(SharedBox):
    value: int = 0
    started: InitVar[Event | None] = None
    release: InitVar[Event | None] = None

    def __post_init__(self, started: Event | None, release: Event | None) -> None:
        if started is not None and release is not None:
            started.set()
            release.wait(20)


def create_until_released(name: str, started: Event, release: Event) -> None:
    with Held.create(name, started=started, release=release):
        pass


def test_creating_a_name_that_is_being_created_says_so(unique_name: str) -> None:
    ctx = mp.get_context("spawn")
    started = ctx.Event()
    release = ctx.Event()
    child = ctx.Process(
        target=create_until_released, args=(unique_name, started, release)
    )
    child.start()
    try:
        assert started.wait(20)
        with pytest.raises(SegmentExistsError) as error:
            Held.create(unique_name)
    finally:
        release.set()
    child.join(20)
    assert child.exitcode == 0
    assert f"being created by pid {child.pid}, which is still running" in str(
        error.value
    )
    assert "unlink" not in str(error.value)


class Pair(SharedBox):
    a: int = 1
    b: int = 2


class PairWithOffset(Pair):
    a: InitVar[int] = 7

    def __post_init__(self, a: int) -> None:
        pass


@dataclass
class PlainPair:
    a: int = 1
    b: int = 2


@dataclass
class PlainPairWithOffset(PlainPair):
    a: InitVar[int] = 7

    def __post_init__(self, a: int) -> None:
        pass


def test_an_initvar_over_an_inherited_field_hides_it(unique_name: str) -> None:
    assert [f.name for f in fields(PairWithOffset)] == [
        f.name for f in dataclasses.fields(PlainPairWithOffset)
    ]
    with PairWithOffset.create(unique_name, b=5) as box:
        with pytest.raises(AttributeError, match="InitVar"):
            _ = box.a
        with pytest.raises(AttributeError, match="InitVar"):
            box.a = 3
        assert box.b == 5


BASE_OPTIONS: dict[str, Any] = {
    "default": 1,
    "factory": {"default_factory": int},
    "options": {
        "default": 1,
        "repr": False,
        "kw_only": True,
        "metadata": {"unit": "mm"},
    },
    "init_false": {"default": 1, "init": False},
}


def described(
    found: tuple[Any, ...],
) -> list[tuple[str, Any, bool, bool, bool, Any, dict[Any, Any]]]:
    return [
        (
            f.name,
            f.default,
            f.default_factory is MISSING,
            f.init,
            f.repr,
            f.kw_only,
            dict(f.metadata),
        )
        for f in found
    ]


@pytest.mark.parametrize("child_hint", [int, InitVar[int]], ids=["field", "initvar"])
@pytest.mark.parametrize("base", BASE_OPTIONS.values(), ids=BASE_OPTIONS.keys())
def test_a_bare_redeclaration_keeps_only_a_plain_inherited_default(
    base: Any, child_hint: Any
) -> None:
    def base_value(make: Any) -> Any:
        return make(**base) if isinstance(base, dict) else base

    def box_base(namespace: dict[str, Any]) -> None:
        namespace.update(__annotations__={"x": int, "y": int}, x=base_value(field), y=2)

    def child_body(namespace: dict[str, Any]) -> None:
        namespace.update(__annotations__={"x": child_hint}, __post_init__=post_init)

    box = types.new_class(
        "Child", (types.new_class("Base", (SharedBox,), {}, box_base),), {}, child_body
    )
    plain_base = dataclass(
        type(
            "Base",
            (),
            {
                "__annotations__": {"x": int, "y": int},
                "x": base_value(dataclasses.field),
                "y": 2,
            },
        )
    )
    plain = dataclass(
        type(
            "Child",
            (plain_base,),
            {"__annotations__": {"x": child_hint}, "__post_init__": post_init},
        )
    )
    assert str(inspect.signature(box)) == str(inspect.signature(plain))
    assert described(fields(cast(type[SharedBox], box))) == described(
        dataclasses.fields(plain)
    )


def test_field_objects_compare_and_hash_by_identity() -> None:
    first, second = field(default=1), field(default=1)
    assert first != second
    assert len({first, second}) == 2
