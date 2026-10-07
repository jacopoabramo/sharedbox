from typing import Annotated

import ml_dtypes
import numpy as np
import pytest

from sharedbox import DType, Shape, SharedBox


class Weights(SharedBox):
    w: Annotated[np.ndarray, Shape(3), DType("bfloat16")] = np.zeros(
        3, ml_dtypes.bfloat16
    )


def test_bfloat16_arrays_read_back_as_bfloat16(unique_name: str) -> None:
    """Check that a bfloat16 numpy field reads back equal and with the bfloat16 dtype."""
    values = np.array([0.5, -1.0, 2.0], ml_dtypes.bfloat16)
    with Weights.create(unique_name, values) as box:
        assert box.w.dtype == ml_dtypes.bfloat16
        assert np.array_equal(box.w, values)
    Weights.unlink(unique_name)


def test_read_into_fills_a_bfloat16_array(unique_name: str) -> None:
    """Fill an ml_dtypes bfloat16 array through its uint16 view."""
    values = np.array([0.5, -1.0, 2.0], ml_dtypes.bfloat16)
    with Weights.create(unique_name, values) as box:
        out = np.zeros(3, ml_dtypes.bfloat16)
        assert box.read_into("w", out) is out
        assert np.array_equal(out, values)
    Weights.unlink(unique_name)


@pytest.mark.parametrize("dtype", [np.float16, np.int16, np.float32])
def test_other_dtypes_are_refused_by_a_bfloat16_field(
    unique_name: str, dtype: type
) -> None:
    """Check that a 2-byte or 4-byte array of another dtype raises TypeError and leaves a bfloat16 field as it was."""
    with Weights.create(unique_name) as box:
        with pytest.raises(TypeError, match="Weights.w"):
            box.w = np.ones(3, dtype)
        assert not box.w.any()
    Weights.unlink(unique_name)
