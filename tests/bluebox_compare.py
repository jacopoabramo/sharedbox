"""Load bluesky document files and compare documents, arrays by their bytes."""

import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

_UUID = re.compile(r"\{\{\s*uuid\s*\}\}[\w-]*")
_ROOT_PATH = re.compile(r"\{\{\s*root_path\s*\}\}")


def load_documents(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Return the documents of a templated file, with uids and the root path filled in.

    Each distinct `{{ uuid }}` placeholder, with the text that follows it up
    to the next character outside a uid, becomes one fresh `uuid4` string, so
    references between documents of the file still match. `{{ root_path }}`
    becomes `/tmp`.
    """
    uids: dict[str, str] = {}
    text = _UUID.sub(
        lambda match: uids.setdefault(match.group(), str(uuid.uuid4())),
        path.read_text(encoding="utf-8"),
    )
    text = _ROOT_PATH.sub("/tmp", text)
    return [(entry["name"], entry["doc"]) for entry in json.loads(text)]


def assert_documents_equal(
    expected: Sequence[tuple[str, Mapping[str, Any]]],
    actual: Sequence[tuple[str, Mapping[str, Any]]],
) -> None:
    """Assert that two sequences of named documents hold the same documents in order.

    Arrays are equal when their dtype, shape and bytes are. A list in
    `expected` equals an array in `actual` when it holds the same values in
    the same shape, but an array in `expected` equals only an array. A numpy
    `float64` equals a `float` of the same value, and NaN equals NaN.
    """
    assert len(actual) == len(expected), f"{len(actual)} documents, not {len(expected)}"
    for index, ((name, doc), (actual_name, actual_doc)) in enumerate(
        zip(expected, actual, strict=True)
    ):
        assert actual_name == name, f"document {index}: {actual_name!r}, not {name!r}"
        _assert_equal(doc, actual_doc, f"document {index} ({name})")


def _assert_equal(expected: object, actual: object, path: str) -> None:
    match expected:
        case np.ndarray():
            assert isinstance(actual, np.ndarray), f"{path}: {actual!r} is not an array"
            assert (actual.dtype, actual.shape) == (expected.dtype, expected.shape), (
                f"{path}: {actual.dtype}{actual.shape}, not {expected.dtype}{expected.shape}"
            )
            assert actual.tobytes() == expected.tobytes(), f"{path}: values differ"
        case Mapping():
            assert isinstance(actual, Mapping), f"{path}: {actual!r} is not a mapping"
            assert actual.keys() == expected.keys(), (
                f"{path}: keys {sorted(actual)}, not {sorted(expected)}"
            )
            for key, value in expected.items():
                _assert_equal(value, actual[key], f"{path}.{key}")
        case list() | tuple() if isinstance(actual, np.ndarray):
            original = np.asarray(expected)
            assert original.shape == actual.shape, (
                f"{path}: shape {actual.shape}, not {original.shape}"
            )
            assert np.array_equal(original, actual), f"{path}: values differ"
        case list() | tuple():
            assert isinstance(actual, list | tuple), f"{path}: {actual!r} is not a list"
            assert len(actual) == len(expected), (
                f"{path}: {len(actual)} items, not {len(expected)}"
            )
            for index, (item, actual_item) in enumerate(
                zip(expected, actual, strict=True)
            ):
                _assert_equal(item, actual_item, f"{path}[{index}]")
        case float():
            assert isinstance(actual, float), f"{path}: {actual!r} is not a float"
            assert actual == expected or (
                math.isnan(actual) and math.isnan(expected)
            ), f"{path}: {actual!r}, not {expected!r}"
        case _:
            assert type(actual) is type(expected) and actual == expected, (
                f"{path}: {actual!r}, not {expected!r}"
            )
