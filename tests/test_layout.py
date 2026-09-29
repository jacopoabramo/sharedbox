from dataclasses import KW_ONLY
from typing import Annotated, ClassVar

import pytest

from sharedbox import SharedBox
from sharedbox._layout import Capacity, build_layout, class_identity


class Sample:
    flag: bool
    count: int
    ratio: float
    label: Annotated[str, Capacity(10)]
    blob: Annotated[bytes, Capacity(3)]
    registry: ClassVar[int] = 0
    _private: int


def test_fields_are_packed_by_alignment() -> None:
    layout = build_layout(Sample)
    assert [(f.name, f.kind, f.offset, f.capacity) for f in layout.fields] == [
        ("flag", "bool", 39, 1),
        ("count", "int", 0, 8),
        ("ratio", "float", 8, 8),
        ("label", "str", 16, 10),
        ("blob", "bytes", 32, 3),
    ]
    assert layout.record_size == 40
    assert layout.by_name["label"].native == (16, 10, 3)
    assert layout.by_name["flag"].native == (39, 1, 0)


def test_check_refuses_what_a_write_would() -> None:
    spec = build_layout(Sample).by_name["count"]
    spec.check(3)
    with pytest.raises(TypeError, match="Sample.count expects int, got str"):
        spec.check("3")


def test_kw_only_leaves_the_schema_hash_unchanged() -> None:
    class Mixed(SharedBox, identity="tests.Mixed"):
        a: int
        _: KW_ONLY
        b: int

    class KwMixed(SharedBox, identity="tests.Mixed", kw_only=True):
        a: int
        b: int

    assert Mixed.__layout__.schema_hash == KwMixed.__layout__.schema_hash


def test_float_field_accepts_int() -> None:
    build_layout(Sample).by_name["ratio"].check(2)


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("flag", 1, TypeError),
        ("count", True, TypeError),
        ("count", 2**63, OverflowError),
        ("ratio", "1.0", TypeError),
        ("label", b"bytes", TypeError),
        ("label", "é" * 6, ValueError),
        ("blob", b"four", ValueError),
    ],
)
def test_encode_rejects(name: str, value: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        build_layout(Sample).by_name[name].check(value)


def test_unsupported_annotation() -> None:
    class Bad:
        items: list[int]

    with pytest.raises(TypeError, match="items"):
        build_layout(Bad)


def test_str_without_capacity() -> None:
    class Bad:
        text: str

    with pytest.raises(TypeError, match="text"):
        build_layout(Bad)


def test_class_without_fields() -> None:
    class Empty:
        pass

    with pytest.raises(TypeError):
        build_layout(Empty)


def test_capacity_bounds() -> None:
    with pytest.raises(ValueError):
        Capacity(0)
    with pytest.raises(ValueError):
        Capacity((1 << 20) + 1)


def test_spawned_main_module_has_the_same_identity() -> None:
    parent = type("Box", (), {"__annotations__": {"x": int}, "__module__": "__main__"})
    child = type(
        "Box", (), {"__annotations__": {"x": int}, "__module__": "__mp_main__"}
    )
    assert class_identity(parent) == class_identity(child) == "__main__.Box"
    assert build_layout(parent).schema_hash == build_layout(child).schema_hash


def test_schema_hash_matches_the_published_vector() -> None:
    motor = type(
        "Motor",
        (),
        {
            "__annotations__": {
                "position": int,
                "enabled": bool,
                "label": Annotated[str, Capacity(32)],
            },
            "__module__": "__main__",
        },
    )
    assert build_layout(motor).schema_hash == 0x82CE467598596A72
    assert (
        build_layout(motor, identity="__main__.Motor").schema_hash == 0x82CE467598596A72
    )
    assert build_layout(motor, identity="motor/2").schema_hash != 0x82CE467598596A72


def test_schema_hash_tracks_layout() -> None:
    class A:
        x: int

    class B:
        x: int

    class A2:
        x: float

    A2.__qualname__ = A.__qualname__
    assert build_layout(A).schema_hash == build_layout(A).schema_hash
    assert build_layout(A).schema_hash != build_layout(B).schema_hash
    assert build_layout(A).schema_hash != build_layout(A2).schema_hash
