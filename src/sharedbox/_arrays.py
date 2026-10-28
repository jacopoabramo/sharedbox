"""Array fields: their shape and element type, and how each array library reads them back."""

import contextlib
import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib.util import find_spec
from typing import Any, Final, Protocol, Self, runtime_checkable

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


def check_dims(dims: object) -> None:
    """Raise `ValueError` unless `dims` is a tuple of 1 to 8 `int`s of at least 1."""
    if (
        not isinstance(dims, tuple)
        or not 1 <= len(dims) <= 8
        or any(not isinstance(d, int) or isinstance(d, bool) or d < 1 for d in dims)
    ):
        raise ValueError(
            f"an array has a tuple of 1 to 8 dimensions of at least 1, got {dims!r}"
        )


@dataclass(frozen=True)
class Shape:
    """Dimensions of an array field, fixed when the class is defined.

    It goes in the annotation, as in
    `Annotated[numpy.ndarray, Shape((480, 640)), DType("uint8")]`. A field of a
    box also takes its sizes from `field(shape=...)` or from a plain default
    array, and they must agree with a `Shape`. An array inside a record or a
    stream item takes its sizes from a `Shape` only. When the annotation
    names a number of dimensions, as in
    `numpy.ndarray[tuple[int, int], numpy.dtype[numpy.uint8]]`, the sizes
    must have as many.

    Raises
    ------
    ValueError
        Unless `dims` is a tuple of 1 to 8 dimensions, each an `int` of at least 1.
    """

    dims: tuple[int, ...]

    def __post_init__(self) -> None:
        check_dims(self.dims)


@dataclass(frozen=True)
class MaxShape:
    """Largest dimensions of an array in a stream item, whose own shape each item stores.

    It goes in the annotation, as in
    `Annotated[numpy.ndarray, MaxShape((2048, 2048)), DType("uint16")]`, and
    an array takes a `MaxShape` or a [`Shape`][sharedbox.Shape], not both.
    An item may hold the array as the item itself or as a member of a record
    or tuple, at any depth, and each value sent has as many dimensions as
    the `MaxShape`, each from 0 to its maximum. A box field and a member of
    an optional, union or collection cannot hold one.

    Raises
    ------
    ValueError
        Unless `dims` is a tuple of 1 to 8 dimensions, each an `int` of at least 1.
    """

    dims: tuple[int, ...]

    def __post_init__(self) -> None:
        check_dims(self.dims)


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


class DType(StrEnum):
    """Element type of an array field.

    Calling it takes a name as numpy spells it (`"float32"`, `"bfloat16"`,
    `"complex64"`, `"bool"`), a `numpy.dtype` or scalar type, or a
    `torch.dtype`, and returns the member of that name, so
    `DType(numpy.uint8)` is `DType("uint8")` is `DType.uint8`, which equals
    the string `"uint8"`. A numpy annotation that names its dtype, as in
    `numpy.ndarray[tuple[int], numpy.dtype[numpy.uint8]]`, needs no `DType`.

    Raises
    ------
    ValueError
        For an element type DLPack cannot describe.
    """

    bool = "bool"
    int8 = "int8"
    int16 = "int16"
    int32 = "int32"
    int64 = "int64"
    uint8 = "uint8"
    uint16 = "uint16"
    uint32 = "uint32"
    uint64 = "uint64"
    float16 = "float16"
    float32 = "float32"
    float64 = "float64"
    bfloat16 = "bfloat16"
    complex64 = "complex64"
    complex128 = "complex128"

    # Enum makes the members with this and looks values up through _missing_; the annotation
    # lets a type checker accept the numpy and torch types a lookup takes.
    def __new__(cls, value: object) -> Self:
        member = str.__new__(cls, value)
        member._value_ = str(value)
        return member

    @classmethod
    def _missing_(cls, value: object) -> "DType":
        name = dtype_name(value)
        if name not in cls.__members__:
            raise ValueError(
                f"unsupported array element type {value!r}; use one of {', '.join(DLPACK)}"
            )
        return cls[name]


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
