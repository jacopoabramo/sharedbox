import dataclasses
import inspect
import types
from dataclasses import KW_ONLY, MISSING, dataclass
from typing import Annotated, Any, ClassVar, cast

import pytest

from sharedbox import Capacity, SharedBox, field, fields


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
        {"__annotations__": {"value": int}, "extra": field(default=0)},
        {
            "__annotations__": {"value": int, "_hidden": int},
            "_hidden": field(default=0),
        },
        {
            "__annotations__": {"value": int, "shared": ClassVar[int]},
            "shared": field(default=0),
        },
    ],
    ids=["no annotation", "underscore name", "ClassVar"],
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


class Overridden(Described):
    count = 3


def test_a_subclass_keeps_its_bases_field_options(unique_name: str) -> None:
    with DescribedChild.create(unique_name, extra=1) as box:
        assert repr(box) == "DescribedChild(count=7, extra=1)"
    assert fields(DescribedChild)[0].metadata == {"k": 1}
    assert [f.kw_only for f in fields(DescribedChild)] == [False, False, True]
    count = fields(Overridden)[1]
    assert (count.default, count.default_factory) == (3, MISSING)


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
    ("box", "plain"),
    [
        (PositionalChild, PlainPositionalChild),
        (KwChild, PlainKwChild),
        (MarkerChild, PlainMarkerChild),
    ],
    ids=["kw_only base", "kw_only child", "KW_ONLY in the base"],
)
def test_kw_only_is_inherited_as_in_a_dataclass(box: type, plain: type) -> None:
    assert str(inspect.signature(box)) == str(inspect.signature(plain))
