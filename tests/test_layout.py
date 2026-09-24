from dataclasses import KW_ONLY
from typing import Annotated, ClassVar

import pytest

from sharedbox._layout import Capacity, build_layout, class_identity


class Sample:
    flag: bool
    count: int
    ratio: float
    label: Annotated[str, Capacity(10)]
    blob: Annotated[bytes, Capacity(3)]
    registry: ClassVar[int] = 0
    _private: int


def test_offsets_are_aligned_and_packed() -> None:
    layout = build_layout(Sample)
    assert [(f.name, f.kind, f.offset, f.capacity) for f in layout.fields] == [
        ("flag", "bool", 0, 1),
        ("count", "int", 8, 8),
        ("ratio", "float", 16, 8),
        ("label", "str", 24, 10),
        ("blob", "bytes", 40, 3),
    ]
    assert layout.record_size == 48
    assert layout.by_name["label"].native == (24, 10, True)
    assert layout.by_name["count"].native.prefixed is False


@pytest.mark.parametrize(
    ("name", "value"),
    [("flag", True), ("count", -(2**63)), ("ratio", 0.25), ("label", "héllo"), ("blob", b"\x00\x01")],
)
def test_encode_decode_round_trip(name: str, value: object) -> None:
    spec = build_layout(Sample).by_name[name]
    assert spec.decode(spec.encode(value)) == value


def test_keyword_only_fields() -> None:
    class Mixed:
        a: int
        _: KW_ONLY
        b: int

    assert [f.kw_only for f in build_layout(Mixed).fields] == [False, True]
    assert [f.kw_only for f in build_layout(Mixed, kw_only=True).fields] == [True, True]
    assert build_layout(Mixed).schema_hash == build_layout(Mixed, kw_only=True).schema_hash


def test_float_field_accepts_int() -> None:
    spec = build_layout(Sample).by_name["ratio"]
    assert spec.decode(spec.encode(2)) == 2.0


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
        build_layout(Sample).by_name[name].encode(value)


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
    child = type("Box", (), {"__annotations__": {"x": int}, "__module__": "__mp_main__"})
    assert class_identity(parent) == class_identity(child) == "__main__.Box"
    assert build_layout(parent).schema_hash == build_layout(child).schema_hash


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
