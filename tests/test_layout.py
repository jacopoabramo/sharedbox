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
    """Check that fields are packed by descending alignment with the expected offsets, capacities and record size."""
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
    """Check that Layout.check accepts an int for an int field and raises TypeError for a str."""
    layout = build_layout(Sample)
    spec = layout.by_name["count"]
    layout.check(spec, 3)
    with pytest.raises(TypeError, match="Sample.count expects int, got str"):
        layout.check(spec, "3")


def test_kw_only_leaves_the_schema_hash_unchanged() -> None:
    """Check that the KW_ONLY marker and the kw_only keyword give the same schema hash."""

    class Mixed(SharedBox, identity="tests.Mixed"):
        a: int
        _: KW_ONLY
        b: int

    class KwMixed(SharedBox, identity="tests.Mixed", kw_only=True):
        a: int
        b: int

    assert Mixed.__layout__.schema_hash == KwMixed.__layout__.schema_hash


def test_float_field_accepts_int() -> None:
    """Check that a float field spec accepts an int."""
    layout = build_layout(Sample)
    layout.check(layout.by_name["ratio"], 2)


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
    """Check that each invalid value for a field spec raises the expected error."""
    layout = build_layout(Sample)
    with pytest.raises(error):
        layout.check(layout.by_name[name], value)


def test_unsupported_annotation() -> None:
    """Check that a field annotated with an unsupported type raises TypeError naming the field."""

    class Bad:
        items: list[int]

    with pytest.raises(TypeError, match="items"):
        build_layout(Bad)


def test_str_without_capacity() -> None:
    """Check that a str field without a Capacity raises TypeError naming the field."""

    class Bad:
        text: str

    with pytest.raises(TypeError, match="text"):
        build_layout(Bad)


def test_class_without_fields() -> None:
    """Check that a class with no fields raises TypeError."""

    class Empty:
        pass

    with pytest.raises(TypeError):
        build_layout(Empty)


def test_capacity_bounds() -> None:
    """Check that Capacity raises ValueError for zero and for more than 1 MiB."""
    with pytest.raises(ValueError):
        Capacity(0)
    with pytest.raises(ValueError):
        Capacity((1 << 20) + 1)


def test_spawned_main_module_has_the_same_identity() -> None:
    """Check that classes in __main__ and __mp_main__ share an identity and a schema hash."""
    parent = type("Box", (), {"__annotations__": {"x": int}, "__module__": "__main__"})
    child = type(
        "Box", (), {"__annotations__": {"x": int}, "__module__": "__mp_main__"}
    )
    assert class_identity(parent) == class_identity(child) == "__main__.Box"
    assert build_layout(parent).schema_hash == build_layout(child).schema_hash


def test_schema_hash_matches_the_published_vector() -> None:
    """Check that the schema hash of a known class matches the published value and changes with the identity."""
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
    """Check that the schema hash is stable for one class and differs across class names and field types."""

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


def test_a_reference_field_is_packed_with_8_byte_fields_and_hashed_by_identity() -> (
    None
):
    """Check that a reference field is packed with the 8-byte fields and hashed by the identity of its class."""

    class Motor(SharedBox, identity="motor/1"):
        position: int

    stage = type(
        "Stage",
        (),
        {
            "__annotations__": {
                "label": Annotated[str, Capacity(4)],
                "motor": Motor | None,
            },
            "__module__": "__main__",
        },
    )
    layout = build_layout(stage)
    assert layout.by_name["motor"].native == (0, 144, 5)
    assert layout.by_name["label"].offset == 144
    assert layout.refs == (layout.by_name["motor"],)
    assert layout.schema_hash == 0xE58153F79AA6FDF6
    required = type(
        "Stage",
        (),
        {
            "__annotations__": {"label": Annotated[str, Capacity(4)], "motor": Motor},
            "__module__": "__main__",
        },
    )
    assert build_layout(required).schema_hash == 0xE33FFD91E10FAB6D
