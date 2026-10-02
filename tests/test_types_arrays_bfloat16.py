from typing import Annotated

import ml_dtypes
import numpy as np

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
