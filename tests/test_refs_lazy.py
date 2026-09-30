"""Collected only from Python 3.14, which evaluates annotations lazily."""

from sharedbox import SharedBox


class Chain(SharedBox):
    value: int = 0
    next: Chain | None = None  # noqa: F821


def test_an_unquoted_self_reference_works_with_lazy_annotations(
    unique_name: str,
) -> None:
    """Check that an unquoted annotation naming the class itself works under lazy annotations."""
    with Chain.create(unique_name) as chain:
        chain.next = chain
        inner = chain.next
        assert inner is not None
        assert inner.name == chain.name
