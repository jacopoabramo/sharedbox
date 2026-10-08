from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import inspect
import os
import sys
import threading
import weakref
from collections.abc import Callable, Generator, Iterator
from dataclasses import MISSING, InitVar
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    NoReturn,
    Protocol,
    Self,
    TypeAlias,
    TypeVar,
    cast,
    dataclass_transform,
    overload,
)

from ._events import BoxEvents, FieldWatch, Watcher, events_class, on_watcher_thread
from ._follow import box_events
from ._layout import (
    Field,
    FieldSpec,
    Layout,
    build_layout,
    class_identity,
    declared,
    field,
    own_annotations,
    own_kw_only,
)
from ._native import (
    BoxClosedError,
    BoxMethod,
    SchemaMismatchError,
    Segment,
    SegmentNotFoundError,
    ValueCache,
)
from ._native import Field as FieldDescriptor
from ._refs import NAME, Reference, attach_reference, box_ref, register, stored

if TYPE_CHECKING:
    if sys.version_info >= (3, 13):
        from types import CapsuleType
    else:
        from typing_extensions import CapsuleType

RESERVED = frozenset(
    {
        "name",
        "closed",
        "close",
        "unlink",
        "update",
        "snapshot",
        "read_into",
        "writing",
        "watch",
        "events",
        "force_unlock",
        "create",
        "attach",
    }
)
# The events group has these as its own names, so no field can take them.
EVENTS_RESERVED = frozenset({"follow", "unfollow", "nested"})
MAX_WAITERS = 4096
# A reference field's cache entry: its create id, the box attached for it, and
# that box's Segment. Segment.cached_ref in _native/module.cpp reads this shape.
RefEntry: TypeAlias = "tuple[int, SharedBox, Segment]"
LIVE: weakref.WeakSet[SharedBox] = weakref.WeakSet()


def check_name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError(f"segment names match {NAME.pattern}, got {name!r}")
    return name


def release(watcher: Watcher, segment: Segment, wait: bool = True) -> None:
    """Stop the box's watcher and detach from its segment.

    Parameters
    ----------
    wait
        If false, the watcher thread is not joined and writes it has not
        seen yet are not delivered; the thread ends on its own once the
        segment is closed.
    """
    try:
        watcher.stop(wait)
    finally:
        segment.close()


def reset_after_fork() -> None:
    """Make every box inherited through `fork` usable in the child."""
    for box in list(LIVE):
        box._segment._after_fork()
        box._values._after_fork()
        box._watcher.after_fork()
        # Another thread of the parent may have held it at the fork.
        box._refs_lock = threading.Lock()
        if box._watcher.follower is not None:
            box._watcher.follower.after_fork()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=reset_after_fork)


B = TypeVar("B", bound="SharedBox")
A = TypeVar("A")


class FactoryDefault:
    """Stands for a factory default in a constructor signature, as in a dataclass's."""

    def __repr__(self) -> str:
        return "<factory>"


FACTORY = FactoryDefault()
REQUIRED = field()


class InitVarAttribute:
    """An `InitVar` on its class; a box has no attribute of that name."""

    def __init__(self, label: str) -> None:
        self.label = label

    def refuse(self) -> NoReturn:
        raise AttributeError(f"{self.label} is an InitVar, which a box does not store")

    def __get__(self, box: object, owner: type | None = None) -> NoReturn:
        self.refuse()

    def __set__(self, box: object, value: object) -> NoReturn:
        self.refuse()

    def __delete__(self, box: object) -> NoReturn:
        self.refuse()


def has_default(param: Field) -> bool:
    return param.default is not MISSING or param.default_factory is not MISSING


def signature(params: list[Field]) -> inspect.Signature:
    """Return the constructor signature a dataclass with these fields and `InitVar` ones would have."""
    return inspect.Signature(
        [
            inspect.Parameter(
                p.name,
                inspect.Parameter.KEYWORD_ONLY
                if p.kw_only
                else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=p.default
                if p.default is not MISSING
                else FACTORY
                if p.default_factory is not MISSING
                else inspect.Parameter.empty,
                annotation=p.type,
            )
            for p in sorted((p for p in params if p.init), key=lambda p: p.kw_only)
        ],
        return_annotation=None,
    )


def fields(class_or_box: type[SharedBox] | SharedBox) -> tuple[Field, ...]:
    """Return one [`Field`][sharedbox.Field] per field of a `SharedBox` subclass or box, in declaration order.

    `InitVar` annotations are left out. This is how a field's `metadata`
    and `doc` are read.

    Raises
    ------
    TypeError
        If the argument is neither a subclass of
        [`SharedBox`][sharedbox.SharedBox] nor one of its boxes.
    """
    cls = class_or_box if isinstance(class_or_box, type) else type(class_or_box)
    if not issubclass(cls, SharedBox) or cls is SharedBox:
        raise TypeError("fields() takes a SharedBox subclass or one of its boxes")
    return tuple(f for f in cls.__sharedbox_init__ if not isinstance(f.type, InitVar))


def install_native_methods(cls: type[SharedBox]) -> None:
    """Set the native update and snapshot on `cls` where it would otherwise inherit SharedBox's.

    If a base's native method would hide an override that `cls` inherits
    from a base later in its MRO, the override is set on `cls` instead.
    """
    layout = cls.__layout__
    names = tuple(sys.intern(spec.name) for spec in layout.fields)
    specs = tuple(spec if spec.target is not None else None for spec in layout.fields)
    segment_slot = SharedBox.__dict__["_segment"]
    for method, kind, helper, follow in (
        ("update", 0, stored, None),
        ("snapshot", 1, box_ref, SharedBox._follow),
    ):
        found = [c.__dict__[method] for c in cls.__mro__ if method in c.__dict__]
        defined = next(m for m in found if not isinstance(m, BoxMethod))
        if defined is SharedBox.__dict__[method]:
            setattr(
                cls,
                method,
                BoxMethod(
                    kind,
                    cls,
                    cls.__qualname__,
                    names,
                    specs,
                    helper,
                    follow,
                    SharedBox.__dict__[method],
                    segment_slot,
                    layout.types,
                ),
            )
        elif isinstance(found[0], BoxMethod):
            # A base earlier in the MRO holds its native method, which would hide this override.
            setattr(cls, method, defined)


def unpickle_box(
    cls: type[B], name: str, schema_hash: int, create_id: int | None = None
) -> B:
    """Attach to the box a pickle refers to, if this process's class has the same fields.

    Parameters
    ----------
    create_id
        If given, the box under `name` must also be the one that was
        pickled, not another created under the same name since.

    Raises
    ------
    SchemaMismatchError
        If this process's class has different fields, or the box under
        `name` is not the one that was pickled.
    """
    if cls._layout().schema_hash != schema_hash:
        raise SchemaMismatchError(
            f"{cls.__qualname__} was pickled by a process whose {cls.__qualname__} "
            "has different fields; both processes must import the same class"
        )
    box = cls.attach(name)
    if create_id is not None and box._segment.create_id != create_id:
        box.close()
        raise SchemaMismatchError(
            f"{cls.__qualname__} was pickled from a different box named {name!r}"
        )
    return box


class SupportsSharedBox(Protocol):
    """An object that hands its shared-memory segment to other extensions.

    [`SharedBox`][sharedbox.SharedBox] is one. Annotate a parameter with it
    in a function that accepts a box.
    """

    def __sharedbox_box__(
        self, max_version: tuple[int, int] | None = None
    ) -> CapsuleType: ...


class _ClassUnlink(Protocol):
    def __call__(self, name: str | None = None) -> None: ...


class Unlink:
    """`Box.unlink(name=None)` on the class, `box.unlink()` on an instance."""

    @overload
    def __get__(self, box: None, owner: type[SharedBox]) -> _ClassUnlink: ...
    @overload
    def __get__(self, box: SharedBox, owner: type[SharedBox]) -> Callable[[], None]: ...
    def __get__(
        self, box: SharedBox | None, owner: type[SharedBox]
    ) -> Callable[..., None]:
        if box is not None:
            return lambda: Segment.unlink(check_name(box.name))
        return lambda name=None: Segment.unlink(
            owner._layout_name() if name is None else check_name(name)
        )


class SharedBoxMeta(type):
    """Give every `SharedBox` subclass an empty `__slots__`, so its instances have no `__dict__`.

    Also record each subclass by schema hash, where reference fields find the
    class of the box they refer to; of several classes with one hash, the
    first one defined that is still alive is used.
    """

    def __new__(
        mcls,
        cls_name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> SharedBoxMeta:
        slots = namespace.setdefault("__slots__", ())
        named = (slots,) if isinstance(slots, str) else slots
        if any(isinstance(base, SharedBoxMeta) for base in bases):
            # A second slot of either name would hide the one every Field reads.
            for taken in ("_segment", "_values"):
                if taken in named:
                    raise TypeError(
                        f"{cls_name}: __slots__ cannot name {taken}, which SharedBox already has"
                    )
        cls = super().__new__(mcls, cls_name, bases, namespace, **kwargs)
        layout = cls.__dict__.get("__layout__")
        if layout is not None:
            register(cast("type[SharedBox]", cls))
        return cls


@dataclass_transform(eq_default=False, field_specifiers=(field,))
class SharedBox(metaclass=SharedBoxMeta):
    """A record whose annotated fields live in a named shared-memory segment.

    Subclass it and annotate the fields. Calling the subclass with the
    field values, as with a dataclass, creates the segment under the
    class's name and writes the values;
    [`create`][sharedbox.SharedBox.create] does the same under another
    name, so one class can describe several boxes, and
    [`attach`][sharedbox.SharedBox.attach] opens an existing box from any
    thread or process. Every process that opens the segment reads and
    writes the same values.

    A field is a public annotation with one of these types:

    | Annotation | Stored as |
    | --- | --- |
    | `bool`, `int`, `float`, `complex` | fixed-size numbers; a `float` field takes an `int` |
    | `Annotated[str, Capacity(n)]`, `bytes`, `bytearray`, `Decimal` | at most `n` bytes |
    | `date`, `time`, `datetime`, `timedelta`, `UUID` | fixed-size values; times naive or with a fixed offset |
    | an `Enum`, `Flag` or `Literal` | the member's position, the bits, or the value's position |
    | an optional `X` (`Optional[X]`), a union | the member the value's type picks |
    | a dataclass, `NamedTuple`, tuple, `TypedDict`, attrs class or `msgspec.Struct` | its members |
    | `Annotated[list[T], Capacity(n)]`, and `set`, `frozenset`, `dict`, `tuple[T, ...]` | at most `n` elements |
    | `Annotated[<array type>, Shape(...), DType(...)]` | the array's elements |

    A `SharedBox` subclass, optional or not, makes a reference field,
    described below. [`Capacity`][sharedbox.Capacity] sets `n`. A read
    returns a new value: changing a list, record or array read from a box
    changes only that copy. Names starting with `_` and `ClassVar`
    annotations are not fields. Fields of a base class come first.

    Fields are positional by default, in declaration order, as in a
    dataclass. Fields declared after a `dataclasses.KW_ONLY` annotation in
    the same class are keyword-only. A default is a plain class attribute
    or given with [`field`][sharedbox.field], and is checked against the
    field's type and capacity when the class is defined.
    `inspect.signature` of the class gives its constructor's parameters; a
    `default_factory` default shows as `<factory>`.

    Assigning a value of the wrong type raises `TypeError`, and a value
    that does not fit its field raises `ValueError`, or `OverflowError`
    for an `int` value out of range for its `int`, `float` or `complex`
    field; either way the stored value does not change. A `bytes` field
    takes `bytes`, `bytearray` and `memoryview` values. Assigning to a
    name that is not a field raises `AttributeError`, and so does deleting
    a field. A box has no `__dict__`: every subclass gets empty
    `__slots__` unless it declares its own. Two boxes are equal only if
    they are the same object.

    A `dataclasses.InitVar[T]` annotation declares a constructor argument
    that is not stored. It takes a position like a field and may have a
    default, as a class attribute or with `field(default=...)`, but no
    `default_factory` and no `init=False`. It is not in the layout, the
    schema hash, [`fields`][sharedbox.fields],
    [`snapshot`][sharedbox.SharedBox.snapshot] or
    [`events`][sharedbox.SharedBox.events]. An `InitVar` named like an
    inherited field removes that field from the subclass, as in
    `dataclasses`; reading or assigning that name on a box raises
    `AttributeError`.

    `__post_init__(self, *initvars)` runs when the class is called and in
    [`create`][sharedbox.SharedBox.create], after the values are written,
    with each `InitVar` value in declaration order. It may read and assign
    fields, reference fields included, and its writes go into the segment.
    It does not run for [`attach`][sharedbox.SharedBox.attach] or
    unpickling. The box is published when `__post_init__` returns, so no
    other process sees it before then. Until then its name is taken:
    creating another box under it raises
    [`SegmentExistsError`][sharedbox.SegmentExistsError], and
    [`attach`][sharedbox.SharedBox.attach], from any process or from
    `__post_init__` itself, waits at most 1 s (the lock timeout, if
    shorter) and then raises
    [`SegmentNotFoundError`][sharedbox.SegmentNotFoundError].
    [`__sharedbox_box__`][sharedbox.SharedBox.__sharedbox_box__] raises
    `BufferError` until then too. If `__post_init__` raises, the box is
    closed and its name removed without ever being published, and the
    exception propagates.

    The class statement takes these keywords:

    - `name`: the segment name of boxes made by calling the class. By
      default it is 16 hex digits of SHA-256 over the class's identity, so
      every process that imports the class uses the same name. It must
      match `[A-Za-z0-9_.-]{1,128}`.
    - `kw_only`: make every field this class declares keyword-only.
      Inherited fields keep the setting of the class that declares them.
    - `lock_timeout`: seconds a read or write waits for a write in
      progress before it raises
      [`LockTimeoutError`][sharedbox.LockTimeoutError]; 5.0 by default,
      finite and in `(0, 86400]`.
    - `identity`: a non-empty string, by default the class's
      `module.qualname`, with `__mp_main__` read as `__main__`. It enters
      the schema hash, and names the box when `name` is not given. A
      subclass does not inherit it. Two classes with the same identity and
      fields share a box, even when they live in different modules; a
      process whose class has another identity cannot attach it.
    - `max_waiters`: how many waiter slots the box has, 1 to 4096, 64 by
      default. Each box handle whose [`watch`][sharedbox.SharedBox.watch]
      or [`events`][sharedbox.SharedBox.events] is in use holds one slot,
      counted across every process. A watcher that finds every slot taken
      logs a warning to the `sharedbox` logger and checks for changes once
      a second until a slot is free.

    A field annotated with a `SharedBox` subclass `X`, or with `X | None`
    (or `Optional[X]`), refers to another box, which keeps its own
    segment, lock and lifetime. The field stores the box's name, schema
    hash and create id. `X` is the class being defined or a class defined
    before it. A field `X` is never empty: assigning `None` raises
    `TypeError`, and reading it returns a box. A field `X | None` may hold
    `None`. The two give different schema hashes, so a class that declares
    one cannot attach a box created by a class that declares the other.
    Defaults work as for any field: `= None`, `field(default=box)` and
    `field(default_factory=...)`. A default box is checked like an
    assigned one when the class is defined, a factory's result at each
    creation. The class keeps the handle given as `field(default=box)` for
    as long as the class exists; once that handle is closed, each creation
    that uses the default raises
    [`BoxClosedError`][sharedbox.BoxClosedError].

    Assigning a box to a reference field stores which box it is, under the
    outer box's lock. A box of `X`, of a subclass of `X`, or of any class
    with `X`'s schema hash (the same identity and fields) is accepted; any
    other value raises `TypeError`, and a closed box raises
    [`BoxClosedError`][sharedbox.BoxClosedError]. The constructor and
    [`update`][sharedbox.SharedBox.update] check reference values the same
    way. Type checkers accept a box of `X` or of a subclass; a box of
    another class with `X`'s schema hash needs a `cast`.

    Reading a reference field attaches the box with its own class, which
    may be a subclass of `X`, and keeps that handle: later reads return
    the same object while the field refers to the same box. The class is
    found by the stored schema hash among the `SharedBox` classes this
    process has defined; of several with one hash, the first one defined
    that is still alive is used. After another thread or process assigns
    another box, the next read attaches that one and closes the handle it
    kept. [`close`][sharedbox.SharedBox.close] on the outer box closes the
    handles its reads attached, and closing a returned box makes the next
    read attach it again. A read raises
    [`UnknownBoxClassError`][sharedbox.UnknownBoxClassError] when this
    process has not imported the module that defines the box's class, and
    [`BrokenReferenceError`][sharedbox.BrokenReferenceError] when the box
    was removed, or removed and created again under the same name (also by
    another class), since it was assigned. A handle that already read the
    field keeps its own mapping of the box and goes on returning it. The
    outer box only points at the other box: closing or unlinking the outer
    box leaves it alone, and no write covers both boxes at once.

    A box can be pickled, and `copy.copy` and `copy.deepcopy` do the same
    as a pickle round trip. The pickle holds the class, the segment name,
    the schema hash and the box's create id, a random number drawn when
    the box was created. Unpickling attaches a new handle: an independent
    box on the same data, which the receiving process closes. It reads
    reference fields from the segment like any other handle. A box pickled
    inside `__post_init__` can be unpickled only once it is published. A
    child process started with `fork` uses the parent's box object, which
    keeps working after the fork.

    Raises
    ------
    TypeError
        When the class is defined, for an annotation of another type, no
        fields or more than 256, a field named like a `SharedBox` member
        (`name`, `closed`, `close`, `unlink`, `update`, `snapshot`,
        `watch`, `events`, `force_unlock`, `create`, `attach`) or like
        `follow`, `unfollow` or `nested`, a default of the wrong type, a
        positional field without a default after one with a default, an
        `InitVar` in a class without `__post_init__`, a reference to a
        class that is not defined yet, `__slots__` that name `_segment` or `_values`, or
        an `identity` that is not a non-empty string. When the class is
        called, for a missing, unknown or repeated value, or a value of
        the wrong type.
    ValueError
        When the class is defined, for a `name` that does not match
        `[A-Za-z0-9_.-]{1,128}`, a `lock_timeout` or `max_waiters` out of
        range, or a default that does not fit its field (any of the cases
        below). When the class is called or a field is assigned, for a
        `str`, `bytes` or `Decimal` value longer than its capacity, a
        collection with more elements than its capacity, a tuple of the
        wrong length, an array of the wrong shape, a `Literal` field given
        another value, flag bits outside 0 to 2**64 - 1, a time or
        datetime whose UTC offset is not whole minutes of less than a day,
        or a `time` whose tzinfo gives no offset without a date.
    OverflowError
        When the class is defined or called, or a field is assigned, with
        an `int` value out of range for its `int`, `float` or `complex`
        field.
    SegmentExistsError
        When the class is called and the name is taken.
    SchemaMismatchError
        When a pickled box is loaded by a process whose class has
        different fields, or the box under that name was unlinked and
        created again since the pickle was made.
    SegmentNotFoundError
        When a pickled box is loaded after its segment is gone.

    Notes
    -----
    On Windows a lock wait ends on a timer tick, so `lock_timeout` can
    expire up to one tick late, 15.6 ms at the default timer resolution.
    The handle a class keeps for a `field(default=box)` default keeps that
    box's segment in existence on Windows, and so does the mapping a
    handle keeps of a box it read through a reference field: other handles
    can still attach it.
    """

    __slots__ = (
        "__weakref__",
        "_finalizer",
        "_refs",
        "_refs_lock",
        "_segment",
        "_values",
        "_watcher",
    )

    __layout__: ClassVar[Layout]
    __sharedbox_options__: ClassVar[dict[str, Field]] = {}
    __sharedbox_init__: ClassVar[tuple[Field, ...]] = ()
    __signature__: ClassVar[inspect.Signature]
    __lock_timeout__: ClassVar[float] = 5.0
    __max_waiters__: ClassVar[int] = 64
    __sharedbox_name__: ClassVar[str]
    __sharedbox_identity__: ClassVar[str]
    __events_class__: ClassVar[type[BoxEvents]]

    def __init_subclass__(
        cls,
        *,
        name: str | None = None,
        kw_only: bool = False,
        lock_timeout: float | None = None,
        max_waiters: int | None = None,
        identity: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        if identity is not None and (not isinstance(identity, str) or not identity):
            raise TypeError(f"{cls.__qualname__}: identity must be a non-empty string")
        cls.__sharedbox_identity__ = (
            class_identity(cls) if identity is None else identity
        )
        layout = build_layout(cls, cls.__sharedbox_identity__)
        clashes = sorted(RESERVED.intersection(layout.by_name))
        if clashes:
            raise TypeError(
                f"{cls.__qualname__}: field name(s) {', '.join(clashes)} clash with SharedBox methods"
            )
        taken = sorted(EVENTS_RESERVED.intersection(layout.by_name))
        if taken:
            raise TypeError(
                f"{cls.__qualname__}: field name(s) {', '.join(taken)} clash with "
                "the events group's follow, unfollow and nested"
            )
        # Set before the fields are checked, so a default box of a field that names
        # cls is compared with the layout of cls rather than that of a base.
        cls.__layout__ = layout
        annotated = own_annotations(cls)
        for attr, value in cls.__dict__.items():
            if isinstance(value, Field) and attr not in annotated:
                raise TypeError(
                    f"{cls.__qualname__}: {attr!r} is a field but has no type annotation"
                )
        found = declared(cls)
        known = {attr for attr, _ in found}
        stray = sorted(
            attr
            for attr, value in cls.__dict__.items()
            if isinstance(value, Field) and attr not in known
        )
        if stray:
            raise TypeError(
                f"{cls.__qualname__}: field() is used for {', '.join(stray)}, which are not fields"
            )
        own = own_kw_only(cls, kw_only)
        # From every base, as dataclasses do: an attribute lookup would see only the first.
        options: dict[str, Field] = {}
        inherited: dict[str, bool] = {}
        for base in reversed(cls.__mro__[1:]):
            options.update(base.__dict__.get("__sharedbox_options__", {}))
            inherited.update(
                (p.name, p.kw_only) for p in base.__dict__.get("__sharedbox_init__", ())
            )
        segment_slot = SharedBox.__dict__["_segment"]
        values_slot = SharedBox.__dict__["_values"]
        params: list[Field] = []
        for attr, hint in found:
            value = cls.__dict__.get(attr, MISSING)
            if isinstance(value, Field):
                options[attr] = value
            elif value is not MISSING and not isinstance(
                value, (FieldDescriptor, Reference)
            ):
                if attr not in own:
                    base = next(
                        b for b in cls.__mro__[1:] if attr in own_annotations(b)
                    )
                    raise TypeError(
                        f"{cls.__qualname__}.{attr} overrides the field inherited from "
                        f"{base.__qualname__} with a plain attribute; declare it with an "
                        f"annotation ({attr}: {inspect.formatannotation(hint)} = {value!r}) or with field()"
                    )
                options[attr] = field(default=value)
            elif value is MISSING and attr in own and attr in options:
                # As in dataclasses, a bare redeclaration keeps only a plain inherited default.
                inherited_default = options[attr].default
                options[attr] = (
                    REQUIRED
                    if inherited_default is MISSING
                    else field(default=inherited_default)
                )
            given = options.get(attr, REQUIRED)
            if attr not in own:
                attr_kw_only = inherited.get(attr, False)
            elif given.kw_only is MISSING:
                attr_kw_only = own[attr]
            else:
                attr_kw_only = given.kw_only
            param = dataclasses.replace(
                given, name=attr, type=hint, kw_only=attr_kw_only
            )
            if isinstance(hint, InitVar):
                if param.default_factory is not MISSING or not param.init:
                    raise TypeError(
                        f"{cls.__qualname__}: InitVar {attr!r} takes neither default_factory nor init=False"
                    )
                # Also hides a descriptor inherited from a base where attr is a field.
                setattr(cls, attr, InitVarAttribute(f"{cls.__qualname__}.{attr}"))
            else:
                spec = layout.by_name[attr]
                if param.default is not MISSING and spec.target is not None:
                    stored(spec, param.default)
                elif param.default is not MISSING:
                    layout.check(spec, param.default)
                if not param.init and not has_default(param):
                    raise TypeError(
                        f"{cls.__qualname__}: field {attr!r} has init=False and no default"
                    )
                setattr(
                    cls,
                    attr,
                    FieldDescriptor(spec, segment_slot, layout.types, values_slot)
                    if spec.target is None
                    else Reference(spec),
                )
            params.append(param)
        if not hasattr(cls, "__post_init__") and any(
            isinstance(p.type, InitVar) for p in params
        ):
            raise TypeError(
                f"{cls.__qualname__} has InitVar fields but no __post_init__"
            )
        seen_default = False
        for param in params:
            if param.kw_only or not param.init:
                continue
            if has_default(param):
                seen_default = True
            elif seen_default:
                raise TypeError(
                    f"{cls.__qualname__}: field {param.name!r} without a default follows a field with one"
                )
        cls.__sharedbox_options__ = options
        cls.__sharedbox_init__ = tuple(params)
        cls.__signature__ = signature(params)
        cls.__sharedbox_name__ = (
            hashlib.sha256(cls.__sharedbox_identity__.encode()).hexdigest()[:16]
            if name is None
            else check_name(name)
        )
        if lock_timeout is not None:
            if not (0 < lock_timeout <= 86400):
                raise ValueError("lock_timeout must be finite and in (0, 86400]")
            cls.__lock_timeout__ = lock_timeout
        if max_waiters is not None:
            if (
                isinstance(max_waiters, bool)
                or not isinstance(max_waiters, int)
                or not 1 <= max_waiters <= MAX_WAITERS
            ):
                raise ValueError(
                    f"max_waiters must be an int between 1 and {MAX_WAITERS}"
                )
            cls.__max_waiters__ = max_waiters
        cls.__events_class__ = events_class(cls, layout)
        install_native_methods(cls)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._open(type(self)._layout_name(), args, kwargs)

    @classmethod
    def create(cls, name: str, /, *args: Any, **kwargs: Any) -> Self:
        """Create a box under `name` instead of the class's name.

        Takes the field values and runs `__post_init__` as calling the
        class does.

        Raises
        ------
        SegmentExistsError
            If the name is taken.
        ValueError
            If `name` does not match `[A-Za-z0-9_.-]{1,128}`.
        """
        box = cls.__new__(cls)
        box._open(check_name(name), args, kwargs)
        return box

    @classmethod
    def attach(cls, name: str | None = None) -> Self:
        """Open the box called `name`, by default the one named after this class.

        `__post_init__` does not run.

        Raises
        ------
        SegmentNotFoundError
            If no segment has that name, or the shared memory under it does
            not become a box within 1 s (the lock timeout, if shorter).
        SchemaMismatchError
            If the segment was created by a different class or a different
            version of this class, uses another major version of the
            segment layout, or has a field of a kind this version cannot
            read.
        ValueError
            If `name` does not match `[A-Za-z0-9_.-]{1,128}`.
        """
        box = cls.__new__(cls)
        layout = cls._layout()
        box._segment = Segment.attach(
            cls._layout_name() if name is None else check_name(name),
            [spec.label for spec in layout.fields],
            layout.schema_hash,
            cls.__lock_timeout__,
            layout.types,
        )
        box._watcher = Watcher(box._segment, layout.types)
        box._track()
        return box

    @classmethod
    def _layout(cls) -> Layout:
        if cls is SharedBox:
            raise TypeError("subclass SharedBox and annotate fields")
        return cls.__layout__

    @classmethod
    def _layout_name(cls) -> str:
        cls._layout()
        return cls.__sharedbox_name__

    def _track(self) -> None:
        self._values = ValueCache(len(type(self).__layout__.fields))
        self._refs: dict[int, RefEntry] = {}
        self._refs_lock = threading.Lock()
        # The finalizer may run on a thread that holds the watcher's lock, which the
        # watcher thread may be waiting for, so it must not join that thread.
        self._finalizer = weakref.finalize(
            self, release, self._watcher, self._segment, False
        )
        LIVE.add(self)

    def _open(self, name: str, args: tuple[Any, ...], values: dict[str, Any]) -> None:
        cls = type(self)
        layout = cls._layout()
        params = [p for p in cls.__sharedbox_init__ if p.init]
        slots = [p.name for p in params if not p.kw_only]
        if len(args) > len(slots):
            raise TypeError(
                f"{cls.__qualname__} takes {len(slots)} positional values, got {len(args)}"
            )
        positional = dict(zip(slots, args))
        twice = sorted(positional.keys() & values.keys())
        if twice:
            raise TypeError(
                f"{cls.__qualname__} got more than one value for {', '.join(twice)}"
            )
        values = {**positional, **values}
        unknown = values.keys() - {p.name for p in params}
        no_init = sorted(unknown & layout.by_name.keys())
        if no_init:
            raise TypeError(
                f"{cls.__qualname__}: field(s) {', '.join(no_init)} have init=False"
            )
        if unknown:
            raise TypeError(
                f"{cls.__qualname__} has no field(s) {', '.join(sorted(unknown))}"
            )
        missing = [
            p.name for p in params if p.name not in values and not has_default(p)
        ]
        if missing:
            raise TypeError(
                f"{cls.__qualname__} is missing value(s) for {', '.join(missing)}"
            )
        for p in cls.__sharedbox_init__:
            if p.name not in values:
                values[p.name] = (
                    p.default if p.default_factory is MISSING else p.default_factory()
                )
        self._segment = Segment.create(
            name,
            [spec.native for spec in layout.fields],
            [spec.label for spec in layout.fields],
            layout.record_size,
            layout.schema_hash,
            cls.__lock_timeout__,
            [
                (
                    spec.index,
                    values[spec.name]
                    if spec.target is None
                    else stored(spec, values[spec.name]),
                )
                for spec in layout.fields
            ],
            cls.__max_waiters__,
            publish=False,
            types=layout.types,
        )
        try:
            self._watcher = Watcher(self._segment, layout.types)
            self._track()
            post_init = getattr(cls, "__post_init__", None)
            if post_init is not None:
                post_init(
                    self,
                    *(
                        values[p.name]
                        for p in cls.__sharedbox_init__
                        if isinstance(p.type, InitVar)
                    ),
                )
            self._segment.publish()
        except BaseException:
            try:
                with contextlib.suppress(SegmentNotFoundError):
                    Segment.unlink(name)
            finally:
                if hasattr(self, "_finalizer"):
                    self.close()
                else:
                    self._segment.close()
            raise

    def _check_names(self, values: dict[str, Any]) -> None:
        unknown = sorted(values.keys() - type(self).__layout__.by_name.keys())
        if unknown:
            raise TypeError(
                f"{type(self).__qualname__} has no field(s) {', '.join(unknown)}"
            ) from None

    @property
    def name(self) -> str:
        """Name of the segment; pass it to [`attach`][sharedbox.SharedBox.attach] in another process."""
        return self._segment.name

    @property
    def closed(self) -> bool:
        """True once [`close`][sharedbox.SharedBox.close] was called on this box."""
        return self._segment.closed

    def update(self, **values: Any) -> None:
        """Write several fields under one lock; a reader sees all of the new values or none.

        Every value is checked before any is written, so an update that
        raises changes nothing. A reference field takes a box or None,
        checked as when it is assigned.

        Raises
        ------
        TypeError
            If a name is not a field or a value has the wrong type.
        ValueError
            If a `str` or `bytes` value is longer than its capacity.
        """
        # A native method replaces this on every class that does not define or inherit its own update.
        layout = type(self).__layout__
        by_name = layout.by_name
        try:
            pairs = [(by_name[n].index, v) for n, v in values.items()]
        except KeyError:
            self._check_names(values)
            raise
        if layout.refs:
            specs = layout.fields
            pairs = [
                (i, v if specs[i].target is None else stored(specs[i], v))
                for i, v in pairs
            ]
        self._segment.set(pairs, layout.types)

    def snapshot(self, *, follow: bool = False) -> dict[str, Any]:
        """Return every field's value, with this box read at one point in time.

        A reference field gives a [`BoxRef`][sharedbox.BoxRef], or None when
        it is empty.

        Parameters
        ----------
        follow
            Replace each reference with the snapshot of the box it refers
            to, itself taken with `follow`. Each box is read at its own
            moment, not together with the others. A box this call has
            already read stays a [`BoxRef`][sharedbox.BoxRef], also when a
            second field refers to it, so a loop of references (a box that
            refers to itself, or A to B and B to A) ends.

        Raises
        ------
        BrokenReferenceError
            With `follow`, if a box referred to no longer exists or was
            created again.
        UnknownBoxClassError
            With `follow`, if no class defined in this process has the
            schema hash of a box referred to.
        """
        # A native method replaces this on every class that does not define or inherit its own snapshot.
        layout = type(self).__layout__
        values = self._segment.get_dict(layout.names, layout.types)
        if layout.refs:
            for spec in layout.refs:
                values[spec.name] = box_ref(values[spec.name])
            if follow:
                self._follow(values)
        return values

    def read_into(self, field: str, out: A) -> A:
        """Copy an array field into `out` and return `out`.

        The copy holds one complete write, as a read does, but goes into
        memory the caller already has, so nothing is allocated.

        Parameters
        ----------
        out
            A writable, C-contiguous array in CPU memory with the field's
            [`DType`][sharedbox.DType] and [`Shape`][sharedbox.Shape]. Its
            contents are unspecified if the call raises.

        Raises
        ------
        TypeError
            If the field is not an array field, or `out` is not a writable,
            C-contiguous array in CPU memory.
        ValueError
            If the box has no such field, or `out` has another dtype or
            shape.
        LockTimeoutError
            If another writer holds the lock longer than the lock timeout.
        BoxClosedError
            If the box is closed.
        """
        spec = self._spec(field)
        if spec.kind != "array":
            raise TypeError(
                f"{spec.label} is not an array field; read_into takes array fields only"
            )
        self._segment.read_into(spec.index, out, type(self).__layout__.types)
        return out

    @contextlib.contextmanager
    def writing(self, field: str) -> Generator[Any, None, None]:
        """Hold the box's write lock and yield the array field to fill in place.

        The block gets an array of the field's declared type that views the
        field in shared memory and holds its current value. Leaving the
        block releases the lock, counts the write and wakes watchers, also
        when the block raises, since the bytes have already changed.
        While the block runs, reads and writes of the box in every process
        wait for the lock, so keep it short. Writing a field of this box
        inside the block waits on that same lock and raises
        [`LockTimeoutError`][sharedbox.LockTimeoutError]. The array keeps
        its own mapping, so using it after the box is closed cannot crash,
        but writing to it after the block takes no lock and tells no one.

        Raises
        ------
        TypeError
            If the field is not an array field.
        ValueError
            If the box has no such field.
        LockTimeoutError
            If another writer holds the lock longer than the lock timeout.
        BoxClosedError
            If the box is closed.
        """
        spec = self._spec(field)
        if spec.kind != "array":
            raise TypeError(
                f"{spec.label} is not an array field; writing takes array fields only"
            )
        writer, view = self._segment.begin_write(
            spec.index, type(self).__layout__.types
        )
        try:
            yield view
        finally:
            writer.end()

    def _follow(self, values: dict[str, Any]) -> None:
        """Replace the references in `values`, read from this box, with nested snapshots, depth first."""
        seen = {(self.name, self._segment.create_id)}
        # A stack instead of recursion, so a chain of boxes can be longer than the recursion limit.
        stack: list[tuple[SharedBox, dict[str, Any], Iterator[FieldSpec]]] = [
            (self, values, iter(type(self).__layout__.refs))
        ]
        while stack:
            box, box_values, specs = stack[-1]
            for spec in specs:
                ref = box_values[spec.name]
                if ref is None or (ref.name, ref.create_id) in seen:
                    continue
                seen.add((ref.name, ref.create_id))
                inner = box._inner(spec, ref.create_id, ref.schema_hash, ref.name)
                box_values[spec.name] = inner_values = inner.snapshot()
                stack.append((inner, inner_values, iter(type(inner).__layout__.refs)))
                break
            else:
                stack.pop()

    def watch(self, field: str) -> FieldWatch[Any]:
        """Return a [`FieldWatch`][sharedbox.FieldWatch] over the values written to `field` from now on.

        A reference field yields a [`BoxRef`][sharedbox.BoxRef] or None.

        Raises
        ------
        ValueError
            If the class has no field `field`.
        """
        spec = self._spec(field)
        return FieldWatch(self._watcher, spec, self._segment.version(spec.index))

    @property
    def events(self) -> BoxEvents:
        """One psygnal signal per field, emitted as `(new, old)` when any thread or process changes it.

        A [`BoxEvents`][sharedbox.BoxEvents] group:
        `box.events.<field>.connect(cb)` listens to one field and
        `box.events.connect(cb)` to all of them.

        Unlike the callbacks of a local evented dataclass, these run on the
        box's watcher thread; connect with `thread="main"` and call
        `psygnal.emit_queued()` to run them on the main thread instead.
        Closing the box delivers writes the watcher thread had not seen
        yet, so callbacks may run once on the thread that calls
        [`close`][sharedbox.SharedBox.close]. A box that is garbage
        collected drops them.

        If several writes happen between two checks by the watcher thread,
        only one emission happens, with the latest value; `old` is the
        value from the last emission. A write that leaves the value
        unchanged emits nothing. For the first emission of a field, `old`
        is the value the field held when `events` was first accessed.

        A callback that raises is logged to the `sharedbox` logger.
        Callbacks connected before it on the same signal already ran;
        callbacks connected after it do not run for that write. Other
        fields still emit. The watcher thread also serves this
        box's [`watch`][sharedbox.SharedBox.watch] iterators, so a slow
        callback delays them. A callback that refers to the box, such as a
        lambda that reads a field, keeps the box alive until
        [`close`][sharedbox.SharedBox.close].

        A field named like an attribute of psygnal's `SignalGroup`
        (`connect`, `disconnect`, `all`, `signals`, `block` and others)
        makes psygnal warn when the class is defined, and
        `box.events.<name>` then returns that attribute;
        `box.events["<name>"]` returns the field's signal.

        psygnal's `blocked`, `paused`, `throttled`, `debounced` and
        `psygnal.qt.start_emitting_from_queue` work on these signals. A
        signal that is blocked drops its emissions in this process, and
        changes made while it is blocked are not emitted later.

        A reference field's signal is emitted when the field is assigned
        another box or emptied, with a [`BoxRef`][sharedbox.BoxRef] or None
        as `new` and `old`. Changes inside the box it refers to are emitted
        by that box's own `events`, and by this group once
        [`follow`][sharedbox.BoxEvents.follow] forwards them.

        A child process created with `fork` while a callback is running
        inherits that signal's lock as held, and hangs the first time it
        connects to, disconnects from or emits that signal.
        """
        cls = type(self)
        return self._watcher.events(
            partial(box_events, cls, self._segment, self._watcher),
            cls.__layout__.fields,
        )

    def _spec(self, field: str) -> FieldSpec:
        try:
            return type(self).__layout__.by_name[field]
        except KeyError:
            raise ValueError(
                f"{type(self).__qualname__} has no field {field!r}"
            ) from None

    def _inner(
        self, spec: FieldSpec, create_id: int, schema_hash: int, name: str
    ) -> SharedBox:
        """Return the box a reference field refers to: the one this handle attached before, or a new one."""
        cached = self._refs.get(spec.index)
        # Reading a dict entry is atomic on every build, so the usual case takes no lock.
        if cached is not None and cached[0] == create_id and not cached[2].closed:
            return cached[1]
        with self._refs_lock:
            cached = self._refs.get(spec.index)
            if cached is not None and cached[0] == create_id and not cached[2].closed:
                return cached[1]
            inner = attach_reference(spec, create_id, schema_hash, name)
            # close() empties the cache after closing the segment, so nothing is cached after it.
            if self._segment.closed:
                inner.close()
                raise BoxClosedError(f"box {self.name!r} is closed")
            self._refs[spec.index] = (create_id, inner, inner._segment)
        if cached is not None:
            cached[1].close()
        return inner

    def force_unlock(self) -> None:
        """Release a write lock left behind by a process that died while writing.

        Reads and writes that wait on such a lock raise
        [`LockTimeoutError`][sharedbox.LockTimeoutError], whose message
        names the process that holds it. This does not check whether the
        process that held the lock is still running: releasing the lock of
        a writer that is still running lets other reads and writes run
        while its write is half done.
        """
        self._segment.force_unlock()

    unlink = Unlink()
    """Remove the segment's name, so no later process can attach to it.

    `Motor.unlink(name=None)` on the class removes `name`, by default the
    class's name; `box.unlink()` on a box removes the name of its segment.
    Boxes already open keep working. On Linux, later
    [`attach`][sharedbox.SharedBox.attach] calls fail. On Windows this does
    nothing; the segment goes away with its last handle.

    Raises
    ------
    ValueError
        If `name` does not match `[A-Za-z0-9_.-]{1,128}`.
    SegmentNotFoundError
        On Linux, if no segment has that name.
    """

    def __sharedbox_box__(
        self, max_version: tuple[int, int] | None = None, **kwargs: Any
    ) -> CapsuleType:
        """Return a `"sharedbox_box"` capsule holding a handle with its own mapping of the segment.

        Python code does not call it; an extension that accepts a box does.
        The capsule holds a pointer to an `sbx_handle`, declared in
        `sharedbox/sharedbox_c.h`. The handle's mapping is made from the
        box's OS handle, so closing or unlinking the box does not affect
        it. A consumer takes the handle with
        `sharedbox::handle::from_capsule` (C++) or `sbx_import` (C),
        renames the capsule `"used_sharedbox_box"`, compares the box's
        schema hash with the one it expects, and destroys (C++) or releases
        (C) the handle itself. A capsule that is never taken releases the
        handle when it is garbage collected. Releasing touches no Python
        objects and does not need the GIL, so it may run on any thread and
        after interpreter shutdown.

        Parameters
        ----------
        max_version
            The `(major, minor)` layout version the caller supports; None
            means the box's own.

        Raises
        ------
        BufferError
            If the major version of `max_version` differs from the box's
            layout, or if called from `__post_init__`, before the box is
            published.
        NotImplementedError
            If any other keyword is given.

        Notes
        -----
        On Windows the segment stays in existence while a handle is held.
        """
        if kwargs:
            raise NotImplementedError(
                f"__sharedbox_box__ does not support {', '.join(sorted(kwargs))}"
            )
        if not self._segment.published:
            raise BufferError(
                "the box is not published yet; call __sharedbox_box__ after __post_init__ returns"
            )
        major, minor = self._segment.layout_version
        if max_version is not None and max_version[0] != major:
            raise BufferError(
                f"box {self.name!r} has layout {major}.{minor}; "
                f"the caller supports major version {max_version[0]}"
            )
        return self._segment._export()

    def close(self) -> None:
        """Detach from the segment; other boxes on it keep working and the data stays.

        Use [`unlink`][sharedbox.SharedBox.unlink] to remove it. Also
        stops this box's watcher thread, closes every box this handle
        attached to read a reference field, and stops all forwarding
        started through its [`events`][sharedbox.SharedBox.events]. Later
        reads and writes raise
        [`BoxClosedError`][sharedbox.BoxClosedError]. Calling it again does
        nothing. Leaving a `with` block calls it.

        Called on a box's watcher thread, from an event callback, it does
        not wait for the watcher threads of the boxes it closes, and drops
        the writes those threads had not seen yet.

        A box that is garbage collected is closed. Forwarding it started
        keeps running until its events group and every group
        [`follow`][sharedbox.BoxEvents.follow] returned are garbage
        collected too, which happens only when the cycle collector runs.
        """
        self._close(not on_watcher_thread())

    def _close(self, wait: bool) -> None:
        # A list instead of recursion, so a chain of boxes can be longer than the recursion limit.
        boxes = [self]
        error: BaseException | None = None
        while boxes:
            box = boxes.pop()
            if box._finalizer.detach() is None:
                continue
            box._values.clear()
            try:
                release(box._watcher, box._segment, wait)
            except BaseException as exc:
                # Keep closing the rest; the first failure is raised once every box is closed.
                error = error or exc
            with box._refs_lock:
                cached, box._refs = box._refs, {}
            boxes.extend(inner for _, inner, _ in cached.values())
            if box._watcher.follower is not None:
                boxes.extend(box._watcher.follower.release())
        if error is not None:
            raise error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(
        self,
    ) -> tuple[Callable[..., SharedBox], tuple[type, str, int, int]]:
        return (
            unpickle_box,
            (
                type(self),
                self.name,
                type(self).__layout__.schema_hash,
                self._segment.create_id,
            ),
        )

    def __repr__(self) -> str:
        if self.closed:
            return f"<{type(self).__qualname__} {self.name!r} closed>"
        values = self.snapshot()
        shown = ", ".join(
            f"{p.name}={values[p.name]!r}"
            for p in type(self).__sharedbox_init__
            if p.repr and p.name in values
        )
        return f"{type(self).__qualname__}({shown})"
