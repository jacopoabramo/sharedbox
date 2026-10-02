import hashlib
import itertools
import keyword
import types
from typing import Annotated, Any, cast

from hypothesis import given
from hypothesis import strategies as st

from sharedbox import Capacity, SharedBox
from sharedbox._box import RESERVED
from sharedbox._layout import build_layout

NAMES = st.from_regex(r"[a-z][a-z0-9_]{0,12}", fullmatch=True).filter(
    lambda n: n not in RESERVED and not keyword.iskeyword(n)
)
KINDS: dict[str, Any] = {"bool": bool, "int": int, "float": float}


@st.composite
def field_lists(draw: st.DrawFn) -> list[tuple[str, str, int]]:
    """1 to 256 fields as (name, kind, capacity), with distinct names."""
    names = draw(st.lists(NAMES, min_size=1, max_size=256, unique=True))
    fields = []
    for name in names:
        kind = draw(st.sampled_from(["bool", "int", "float", "str", "bytes"]))
        capacity = draw(
            st.one_of(st.integers(1, 64), st.integers(1, 1 << 20))
            if kind in ("str", "bytes")
            else st.just({"bool": 1, "int": 8, "float": 8}[kind])
        )
        fields.append((name, kind, capacity))
    return fields


def annotations(fields: list[tuple[str, str, int]]) -> dict[str, Any]:
    return {
        name: KINDS[kind]
        if kind in KINDS
        else Annotated[str if kind == "str" else bytes, Capacity(capacity)]
        for name, kind, capacity in fields
    }


def plain_class(fields: list[tuple[str, str, int]]) -> type:
    return type(
        "Random", (), {"__annotations__": annotations(fields), "__module__": "props"}
    )


@given(fields=field_lists())
def test_random_classes_lay_out_without_overlap(
    fields: list[tuple[str, str, int]],
) -> None:
    """Check that random field lists give aligned, non-overlapping fields inside the record with a stable schema hash."""
    layout = build_layout(plain_class(fields))
    assert [(s.name, s.kind, s.capacity) for s in layout.fields] == fields
    spans = []
    for spec in layout.fields:
        assert spec.type is not None
        assert spec.offset % spec.type.alignment == 0
        end = spec.offset + spec.capacity + (4 if spec.kind in ("str", "bytes") else 0)
        assert end <= layout.record_size
        spans.append((spec.offset, end))
    spans.sort()
    for (_, end), (start, _) in itertools.pairwise(spans):
        assert end <= start
    assert layout.record_size % 8 == 0
    assert build_layout(plain_class(fields)).schema_hash == layout.schema_hash


@given(fields=field_lists(), identity=st.text(min_size=1, max_size=40))
def test_an_identity_names_the_box(
    fields: list[tuple[str, str, int]], identity: str
) -> None:
    """Check that the box name is derived from the identity and equal identities give the same schema hash."""

    def body(namespace: dict[str, object]) -> None:
        namespace["__annotations__"] = annotations(fields)

    first = cast(
        type[SharedBox],
        types.new_class("Random", (SharedBox,), {"identity": identity}, body),
    )
    second = cast(
        type[SharedBox],
        types.new_class("Other", (SharedBox,), {"identity": identity}, body),
    )
    assert first._layout_name() == hashlib.sha256(identity.encode()).hexdigest()[:16]
    assert first.__layout__.schema_hash == second.__layout__.schema_hash
