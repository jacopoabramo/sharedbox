from typing import Any

import pytest
from bluebox_compare import Document, assert_documents_equal


def test_int_timestamp_of_an_event_equals_a_float() -> None:
    """Accept an int time and timestamps in an event and an event page against equal floats."""
    expected: list[Document] = [
        ("event", {"time": 5, "timestamps": {"x": 6}}),
        ("event_page", {"time": [5], "timestamps": {"x": [6]}}),
    ]
    actual: list[Document] = [
        ("event", {"time": 5.0, "timestamps": {"x": 6.0}}),
        ("event_page", {"time": [5.0], "timestamps": {"x": [6.0]}}),
    ]

    assert_documents_equal(expected, actual)


@pytest.mark.parametrize(
    ("name", "expected", "actual"),
    [
        ("start", {"time": 5}, {"time": 5.0}),
        ("stop", {"time": 5}, {"time": 5.0}),
        ("descriptor", {"timestamps": {"x": 6}}, {"timestamps": {"x": 6.0}}),
        ("event", {"data": {"time": 5}}, {"data": {"time": 5.0}}),
        ("event", {"data": {"timestamps": {"x": 6}}}, {"data": {"timestamps": {"x": 6.0}}}),
    ],
    ids=["start", "stop", "descriptor", "data key time", "data key timestamps"],
)
def test_int_against_float_is_refused_elsewhere(
    name: str, expected: dict[str, Any], actual: dict[str, Any]
) -> None:
    """Refuse an int that became a float outside the time and timestamps of an event."""
    with pytest.raises(AssertionError, match="is not an? ?float|not 5|not 6"):
        assert_documents_equal([(name, expected)], [(name, actual)])
