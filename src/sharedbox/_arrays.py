"""Array fields: their shape and element type, and how each array library reads them back."""

import contextlib
import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, Final, Protocol, runtime_checkable

DLPACK: Final[dict[str, tuple[int, int]]] = {
    "bool": (6, 8),
    "int8": (0, 8),
    "int16": (0, 16),
    "int32": (0, 32),
    "int64": (0, 64),
    "uint8": (1, 8),
    "uint16": (1, 16),
    "uint32": (1, 32),
    "uint64": (1, 64),
    "float16": (2, 16),
    "float32": (2, 32),
    "float64": (2, 64),
    "bfloat16": (4, 16),
    "complex64": (5, 64),
    "complex128": (5, 128),
}


@runtime_checkable
class SupportsDLPack(Protocol):
    """An object another library can import through DLPack, such as `numpy.from_dlpack` takes.

    A field annotated with it reads back sharedbox's own array object,
    which implements it.
    """

    def __dlpack__(
        self,
        *,
        stream: Any = None,
        max_version: tuple[int, int] | None = None,
        dl_device: tuple[int, int] | None = None,
        copy: bool | None = None,
    ) -> Any: ...

    def __dlpack_device__(self) -> tuple[int, int]: ...


@dataclass(frozen=True, init=False)
class Shape:
    """Dimensions of an array field, fixed when the class is defined.

    It goes in the annotation, as in
    `Annotated[numpy.ndarray, Shape(480, 640), DType("uint8")]`. A
    field with a plain default array needs no `Shape`: its shape is the
    default's. When the annotation names a number of dimensions, as in
    `numpy.ndarray[tuple[int, int], numpy.dtype[numpy.uint8]]`, the `Shape`
    or the default must have as many.

    Raises
    ------
    ValueError
        Unless there are 1 to 8 dimensions, each an `int` of at least 1.
    """

    dims: tuple[int, ...]

    def __init__(self, *dims: int) -> None:
        if not 1 <= len(dims) <= 8 or any(
            not isinstance(d, int) or isinstance(d, bool) or d < 1 for d in dims
        ):
            raise ValueError(
                f"an array has 1 to 8 dimensions of at least 1, got {dims}"
            )
        object.__setattr__(self, "dims", dims)


def dtype_name(dtype: object) -> str:
    """Return the name of a dtype given as a string, a numpy dtype or scalar type, or a torch dtype."""
    if isinstance(dtype, str):
        return dtype
    if isinstance(dtype, type):
        # A numpy scalar type's name can differ from its dtype's: numpy.intc is int32.
        if dtype.__module__ == "numpy":
            with contextlib.suppress(TypeError):
                return str(sys.modules["numpy"].dtype(dtype).name)
        return dtype.__name__
    return str(dtype).removeprefix("torch.")


@dataclass(frozen=True, init=False)
class DType:
    """Element type of an array field.

    It takes a name as numpy spells it (`"float32"`, `"bfloat16"`,
    `"complex64"`, `"bool"`), a `numpy.dtype` or scalar type, or a
    `torch.dtype`. A numpy annotation that names its dtype, as in
    `numpy.ndarray[tuple[int], numpy.dtype[numpy.uint8]]`, needs no `DType`.

    Raises
    ------
    ValueError
        For an element type DLPack cannot describe.
    """

    name: str

    def __init__(self, dtype: object) -> None:
        name = dtype_name(dtype)
        if name not in DLPACK:
            raise ValueError(
                f"unsupported array element type {dtype!r}; use one of {', '.join(DLPACK)}"
            )
        object.__setattr__(self, "name", name)


CONVERTERS: dict[str, Callable[[SupportsDLPack], Any]] = {}


def key(array_type: type) -> str:
    """Return the registry key of `array_type`, so a library is found without importing it."""
    return f"{array_type.__module__}.{array_type.__qualname__}"


def register_array_type(
    array_type: type, from_dlpack: Callable[[SupportsDLPack], Any]
) -> None:
    """Read fields annotated with `array_type` back through `from_dlpack`.

    `from_dlpack` receives the array a read copied out of the box and
    returns one of `array_type`. Register before defining a class that
    names the type; numpy and torch need no registration.
    """
    CONVERTERS[key(array_type)] = from_dlpack


def converter(
    array_type: type, name: str, where: str
) -> tuple[Callable[[SupportsDLPack], Any] | None, bool]:
    """Return the function that turns a read array into `array_type`, and whether it expects uint16 elements in place of bfloat16.

    Raises
    ------
    TypeError
        For a type with no converter, or a bfloat16 numpy field without
        the ml_dtypes package.
    """
    if array_type is SupportsDLPack:
        return None, False
    found = key(array_type)
    if found in CONVERTERS:
        return CONVERTERS[found], False
    if found == "numpy.ndarray":
        numpy = sys.modules["numpy"]
        if name != "bfloat16":
            return numpy.from_dlpack, False
        if find_spec("ml_dtypes") is None:
            raise TypeError(
                f"{where}: a bfloat16 numpy array needs the ml_dtypes package"
            )
        bfloat16 = importlib.import_module("ml_dtypes").bfloat16
        return (lambda array: numpy.from_dlpack(array).view(bfloat16)), True
    if found == "torch.Tensor":
        return sys.modules["torch"].from_dlpack, False
    raise TypeError(
        f"{where}: no converter reads arrays back as {found}; call register_array_type first"
    )
