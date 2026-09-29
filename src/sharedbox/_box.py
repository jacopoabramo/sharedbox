from __future__ import annotations

import dataclasses
import hashlib
import inspect
import os
import re
import sys
import weakref
from collections.abc import Callable
from dataclasses import MISSING
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Protocol,
    Self,
    TypeVar,
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
    own_kw_only,
)
from ._native import LAYOUT_VERSION, SchemaMismatchError, Segment
from ._native import Field as FieldDescriptor

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
LIVE: weakref.WeakSet[SharedBox] = weakref.WeakSet()


def check_name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError(f"segment names match {NAME.pattern}, got {name!r}")
    return name


def release(watcher: Watcher, segment: Segment, wait: bool = True) -> None:
    """Stop the box's watcher and detach from its segment.

    With ``wait`` false the watcher thread is not joined and writes it has not
    seen yet are not delivered; the thread ends on its own once the segment is
    closed.
    """
    try:
        watcher.stop(wait)
    finally:
        segment.close()


def reset_after_fork() -> None:
    """Make every box inherited through ``fork`` usable in the child."""
    for box in list(LIVE):
        box._segment._after_fork()
        box._watcher.after_fork()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=reset_after_fork)


B = TypeVar("B", bound="SharedBox")


class FactoryDefault:
    """Stands for a factory default in a constructor signature, as in a dataclass's."""

    def __repr__(self) -> str:
        return "<factory>"


FACTORY = FactoryDefault()
REQUIRED = field()


def has_default(param: Field) -> bool:
    return param.default is not MISSING or param.default_factory is not MISSING


def signature(params: list[Field]) -> inspect.Signature:
    """The constructor signature a dataclass with these fields would have."""
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
    """Every field of a ``SharedBox`` subclass or box, in declaration order."""
    cls = class_or_box if isinstance(class_or_box, type) else type(class_or_box)
    if not issubclass(cls, SharedBox) or cls is SharedBox:
        raise TypeError("fields() takes a SharedBox subclass or one of its boxes")
    return cls.__sharedbox_init__


def unpickle_box(
    cls: type[B], name: str, schema_hash: int, create_id: int | None = None
) -> B:
    """Attach to the box a pickle refers to, if this process's class has the same fields.

    With ``create_id``, the box under ``name`` must also be the one that was
    pickled, not another created under the same name since.
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
    """An object that hands its shared-memory segment to other extensions, as :class:`SharedBox` does."""

    def __sharedbox_box__(
        self, max_version: tuple[int, int] | None = None
    ) -> CapsuleType: ...


class _ClassUnlink(Protocol):
    def __call__(self, name: str | None = None) -> None: ...


class Unlink:
    """``Box.unlink(name=None)`` on the class, ``box.unlink()`` on an instance."""

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
    """Give every ``SharedBox`` subclass an empty ``__slots__``, so its instances have no ``__dict__``."""

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
        return super().__new__(mcls, cls_name, bases, namespace, **kwargs)


@dataclass_transform(eq_default=False, field_specifiers=(field,))
class SharedBox(metaclass=SharedBoxMeta):
    """A record whose annotated fields live in a named shared-memory segment.

    Subclass it and annotate fields with ``bool``, ``int``, ``float``,
    ``Annotated[str, Capacity(n)]`` or ``Annotated[bytes, Capacity(n)]``.
    Calling the subclass with the field values, as with a dataclass, creates
    the segment; :meth:`attach` opens it from any thread or process. The
    segment is named after the class's identity (the ``identity`` class
    keyword, by default ``module.qualname``) unless the ``name`` class
    keyword says otherwise; :meth:`create` makes further boxes under
    explicit names.
    """

    __slots__ = ("__weakref__", "_finalizer", "_segment", "_watcher")

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
            elif value is not MISSING and not isinstance(value, FieldDescriptor):
                if attr not in own:
                    base = next(
                        b for b in cls.__mro__[1:] if attr in inspect.get_annotations(b)
                    )
                    raise TypeError(
                        f"{cls.__qualname__}.{attr} overrides the field inherited from "
                        f"{base.__qualname__} with a plain attribute; declare it with an "
                        f"annotation ({attr}: {inspect.formatannotation(hint)} = {value!r}) or with field()"
                    )
                options[attr] = field(default=value)
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
            spec = layout.by_name[attr]
            if param.default is not MISSING:
                spec.check(param.default)
            if not param.init and not has_default(param):
                raise TypeError(
                    f"{cls.__qualname__}: field {attr!r} has init=False and no default"
                )
            setattr(cls, attr, FieldDescriptor(spec, segment_slot))
            params.append(param)
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
        cls.__layout__ = layout
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
        """Create a box under ``name`` instead of the class's name; fails if the name is taken."""
        box = cls.__new__(cls)
        box._open(check_name(name), args, kwargs)
        return box

    @classmethod
    def attach(cls, name: str | None = None) -> Self:
        """Open the box called ``name``, by default the one named after this class."""
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
            [(spec.index, values[spec.name]) for spec in layout.fields],
            cls.__max_waiters__,
        )
        self._watcher = Watcher(self._segment)
        self._track()

    def _check_names(self, values: dict[str, Any]) -> None:
        unknown = sorted(values.keys() - type(self).__layout__.by_name.keys())
        if unknown:
            raise TypeError(
                f"{type(self).__qualname__} has no field(s) {', '.join(unknown)}"
            ) from None

    @property
    def name(self) -> str:
        """Name of the segment; pass it to :meth:`attach` in another process."""
        return self._segment.name

    @property
    def closed(self) -> bool:
        """True after :meth:`close`."""
        return self._segment.closed

    def update(self, **values: Any) -> None:
        """Write several fields at once; readers see all of them or none."""
        by_name = type(self).__layout__.by_name
        try:
            pairs = [(by_name[n].index, v) for n, v in values.items()]
        except KeyError:
            self._check_names(values)
            raise
        self._segment.set(pairs)

    def snapshot(self) -> dict[str, Any]:
        """Every field's value, read at one point in time."""
        return self._segment.get_dict(type(self).__layout__.names)

    def watch(self, field: str) -> FieldWatch[Any]:
        """Iterate over values written to ``field`` from now on."""
        spec = self._spec(field)
        return FieldWatch(self._watcher, spec, self._segment.version(spec.index))

    @property
    def events(self) -> SignalGroup:
        """One psygnal signal per field, emitted as ``(new, old)`` when any thread or process changes it.

        Differs from a local evented dataclass in these ways:

        Callbacks run on the box's watcher thread; connect with
        ``thread="main"`` and call ``psygnal.emit_queued()`` to run them on
        the main thread instead. Closing the box delivers writes the watcher
        thread had not seen yet, so callbacks may run once on the thread
        that calls :meth:`close`, or on the thread that garbage collects
        the box.

        If several writes happen between two checks by the watcher thread,
        only one emission happens, with the latest value; ``old`` is the
        value from the last emission. A write that leaves the value
        unchanged emits nothing. For the first emission of a field, ``old``
        is the value the field held when ``events`` was first accessed.

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

    def force_unlock(self) -> None:
        """Release a write lock left behind by a process that died while writing."""
        self._segment.force_unlock()

    unlink = Unlink()

    def __sharedbox_box__(
        self, max_version: tuple[int, int] | None = None, **kwargs: Any
    ) -> CapsuleType:
        """A ``"sharedbox_box"`` capsule holding a handle with its own mapping of the segment.

        Closing or unlinking the box does not affect the handle. A
        ``max_version`` whose major differs from the box's layout raises
        ``BufferError``; any other keyword raises ``NotImplementedError``.
        """
        if kwargs:
            raise NotImplementedError(
                f"__sharedbox_box__ does not support {', '.join(sorted(kwargs))}"
            )
        major, minor = LAYOUT_VERSION
        if max_version is not None and max_version[0] != major:
            raise BufferError(
                f"box {self.name!r} has layout {major}.{minor}; "
                f"the caller supports major version {max_version[0]}"
            )
        return self._segment._export()

    def close(self) -> None:
        """Detach from the segment; other boxes keep it. Use :meth:`unlink` to remove it."""
        if self._finalizer.detach() is not None:
            release(self._watcher, self._segment)

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
            if p.repr
        )
        return f"{type(self).__qualname__}({shown})"
