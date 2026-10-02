from typing import Annotated

import torch

from sharedbox import DType, Shape, SharedBox


class Tensor(SharedBox):
    t: Annotated[torch.Tensor, Shape(2, 2), DType(torch.float32)] = torch.zeros(2, 2)


def test_a_torch_field_reads_back_as_a_tensor(unique_name: str) -> None:
    """Check that a torch field takes a tensor and reads back a tensor with the same values."""
    values = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    with Tensor.create(unique_name, values) as box:
        assert isinstance(box.t, torch.Tensor)
        assert torch.equal(box.t, values)
    Tensor.unlink(unique_name)
