from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import inspect
import os
import re
import sys
import threading
import weakref
from collections.abc import Callable, Iterator
from dataclasses import MISSING, InitVar
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

from psygnal import SignalGroup

from ._events import FieldWatch, Watcher, events_class
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
    LAYOUT_VERSION,
    BoxClosedError,
    SchemaMismatchError,
    Segment,
    SegmentNotFoundError,
)
from ._native import Field as FieldDescriptor
from ._refs import Reference, attach_reference, box_ref, register, stored

if TYPE_CHECKING:
    if sys.version_info >= (3, 13):
        from types import CapsuleType
    else:
        from typing_extensions import CapsuleType

NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")
RESERVED = frozenset(
    {
        "name",
        "closed",
        "close",
        "unlink",
        "update",
        "snapshot",
        "watch",
        "events",
        "force_unlock",
        "create",
        "attach",
    }
)
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
        box._watcher.after_fork()
        # Another thread of the parent may have held it at the fork.
        box._refs_lock = threading.Lock()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=reset_after_fork)


B = TypeVar("B", bound="SharedBox")


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
    """The constructor signature a dataclass with these fields and `InitVar` ones would have."""
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
    """Every field of a `SharedBox` subclass or box, in declaration order.

    `InitVar` annotations are left out.

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

    [`SharedBox`][sharedbox.SharedBox] is one.
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
        # A second _segment slot would hide the one every Field reads.
        if any(isinstance(base, SharedBoxMeta) for base in bases) and "_segment" in (
            (slots,) if isinstance(slots, str) else slots
        ):
            raise TypeError(
                f"{cls_name}: __slots__ cannot name _segment, which SharedBox already has"
            )
        cls = super().__new__(mcls, cls_name, bases, namespace, **kwargs)
        layout = cls.__dict__.get("__layout__")
        if layout is not None:
            register(cast("type[SharedBox]", cls))
        return cls


@dataclass_transform(eq_default=False, field_specifiers=(field,))
class SharedBox(metaclass=SharedBoxMeta):
    """A record whose annotated fields live in a named shared-memory segment.

    Subclass it and annotate fields with `bool`, `int`, `float`,
    `Annotated[str, Capacity(n)]`, `Annotated[bytes, Capacity(n)]`, or a
    `SharedBox` subclass `X` or `X | None` for a reference to another box.
    Calling the subclass with the field values, as with a dataclass,
    creates the segment; [`attach`][sharedbox.SharedBox.attach] opens it
    from any thread or process. The segment is named after the class's
    identity (the `identity` class keyword, by default `module.qualname`)
    unless the `name` class keyword says otherwise;
    [`create`][sharedbox.SharedBox.create] makes further boxes under
    explicit names.
    """

    __slots__ = (
        "__weakref__",
        "_finalizer",
        "_refs",
        "_refs_lock",
        "_segment",
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
    __events_class__: ClassVar[type[SignalGroup]]

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
        options = dict(cls.__sharedbox_options__)
        own = own_kw_only(cls, kw_only)
        inherited: dict[str, bool] = {}
        for base in reversed(cls.__mro__[1:]):
            inherited.update(
                (p.name, p.kw_only) for p in base.__dict__.get("__sharedbox_init__", ())
            )
        segment_slot = SharedBox.__dict__["_segment"]
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
                    spec.check(param.default)
                if not param.init and not has_default(param):
                    raise TypeError(
                        f"{cls.__qualname__}: field {attr!r} has init=False and no default"
                    )
                setattr(
                    cls,
                    attr,
                    FieldDescriptor(spec, segment_slot)
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._open(type(self)._layout_name(), args, kwargs)

    @classmethod
    def create(cls, name: str, /, *args: Any, **kwargs: Any) -> Self:
        """Create a box under `name` instead of the class's name.

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

        Raises
        ------
        SegmentNotFoundError
            If no segment has that name.
        SchemaMismatchError
            If the segment was created by a different class or layout.
        """
        box = cls.__new__(cls)
        layout = cls._layout()
        box._segment = Segment.attach(
            cls._layout_name() if name is None else check_name(name),
            [spec.label for spec in layout.fields],
            layout.schema_hash,
            cls.__lock_timeout__,
        )
        box._watcher = Watcher(box._segment)
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
        )
        try:
            self._watcher = Watcher(self._segment)
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
        """True after [`close`][sharedbox.SharedBox.close]."""
        return self._segment.closed

    def update(self, **values: Any) -> None:
        """Write several fields at once; readers see all of them or none."""
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
        self._segment.set(pairs)

    def snapshot(self, *, follow: bool = False) -> dict[str, Any]:
        """Every field's value; this box is read at one point in time.

        A reference field gives a [`BoxRef`][sharedbox.BoxRef], or None when
        it is empty.

        Parameters
        ----------
        follow
            Replace each reference with the snapshot of the box it refers
            to, itself taken with `follow`. Each box is read at its own
            moment, not together with the others. A box this call has
            already read stays a [`BoxRef`][sharedbox.BoxRef], also when a
            second field refers to it, so a loop of references ends.

        Raises
        ------
        BrokenReferenceError
            With `follow`, if a box referred to no longer exists or was
            created again.
        UnknownBoxClassError
            With `follow`, if no class defined in this process has the
            schema hash of a box referred to.
        """
        layout = type(self).__layout__
        values = self._segment.get_dict(layout.names)
        if layout.refs:
            for spec in layout.refs:
                values[spec.name] = box_ref(values[spec.name])
            if follow:
                self._follow(values)
        return values

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
        """Iterate over values written to `field` from now on."""
        spec = self._spec(field)
        return FieldWatch(self._watcher, spec, self._segment.version(spec.index))

    @property
    def events(self) -> SignalGroup:
        """One psygnal signal per field, emitted as `(new, old)` when any thread or process changes it.

        Differs from a local evented dataclass in these ways:

        Callbacks run on the box's watcher thread; connect with
        `thread="main"` and call `psygnal.emit_queued()` to run them on the
        main thread instead. Closing the box delivers writes the watcher
        thread had not seen yet, so callbacks may run once on the thread
        that calls [`close`][sharedbox.SharedBox.close], or on the thread
        that garbage collects the box.

        If several writes happen between two checks by the watcher thread,
        only one emission happens, with the latest value; `old` is the
        value from the last emission. A write that leaves the value
        unchanged emits nothing. For the first emission of a field, `old`
        is the value the field held when `events` was first accessed.

        A callback that raises is logged. Callbacks connected before it on
        the same signal already ran; callbacks connected after it do not
        run for that write. Other fields still emit normally.
        """
        return self._watcher.events(
            type(self).__events_class__, type(self).__layout__.fields
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
        """The box a reference field refers to: the one this handle attached before, or a new one."""
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
        """Release a write lock left behind by a process that died while writing."""
        self._segment.force_unlock()

    unlink = Unlink()

    def __sharedbox_box__(
        self, max_version: tuple[int, int] | None = None, **kwargs: Any
    ) -> CapsuleType:
        """A `"sharedbox_box"` capsule holding a handle with its own mapping of the segment.

        Closing or unlinking the box does not affect the handle.

        Raises
        ------
        BufferError
            If the major version of `max_version` differs from the box's
            layout, or if called from `__post_init__`, before the box is
            published.
        NotImplementedError
            If any other keyword is given.
        """
        if kwargs:
            raise NotImplementedError(
                f"__sharedbox_box__ does not support {', '.join(sorted(kwargs))}"
            )
        if not self._segment.published:
            raise BufferError(
                "the box is not published yet; call __sharedbox_box__ after __post_init__ returns"
            )
        major, minor = LAYOUT_VERSION
        if max_version is not None and max_version[0] != major:
            raise BufferError(
                f"box {self.name!r} has layout {major}.{minor}; "
                f"the caller supports major version {max_version[0]}"
            )
        return self._segment._export()

    def close(self) -> None:
        """Detach from the segment; other boxes keep it.

        Use [`unlink`][sharedbox.SharedBox.unlink] to remove it. Also
        closes every box this handle attached to read a reference field.
        """
        # A list instead of recursion, so a chain of boxes can be longer than the recursion limit.
        boxes = [self]
        while boxes:
            box = boxes.pop()
            if box._finalizer.detach() is None:
                continue
            try:
                release(box._watcher, box._segment)
            finally:
                with box._refs_lock:
                    cached, box._refs = box._refs, {}
                boxes.extend(inner for _, inner, _ in cached.values())

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
