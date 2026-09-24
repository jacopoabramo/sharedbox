from __future__ import annotations

import hashlib
import re
import weakref
from collections.abc import Callable
from typing import Any, ClassVar, Protocol, Self, dataclass_transform, overload

from psygnal import SignalGroup

from ._events import FieldWatch, Watcher, events_class
from ._layout import FieldSpec, Layout, build_layout, class_identity
from ._native import Segment

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
MISSING = object()


def check_name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError(f"segment names match {NAME.pattern}, got {name!r}")
    return name


def release(watcher: Watcher, segment: Segment) -> None:
    """Stop the box's watcher and detach from its segment."""
    watcher.stop()
    segment.close()


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


class Field:
    """Reads and writes one field of the box it is accessed through."""

    __slots__ = ("spec",)

    def __init__(self, spec: FieldSpec) -> None:
        self.spec = spec

    def __get__(self, box: SharedBox | None, owner: type | None = None) -> Any:
        if box is None:
            return self
        return self.spec.decode(box._segment.read(self.spec.index))

    def __set__(self, box: SharedBox, value: Any) -> None:
        box._segment.write([(self.spec.index, self.spec.encode(value))])


class SharedBoxMeta(type):
    """Give every ``SharedBox`` subclass an empty ``__slots__``, so its instances have no ``__dict__``."""

    def __new__(
        mcls,
        cls_name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> SharedBoxMeta:
        namespace.setdefault("__slots__", ())
        return super().__new__(mcls, cls_name, bases, namespace, **kwargs)


@dataclass_transform(eq_default=False)
class SharedBox(metaclass=SharedBoxMeta):
    """A record whose annotated fields live in a named shared-memory segment.

    Subclass it and annotate fields with ``bool``, ``int``, ``float``,
    ``Annotated[str, Capacity(n)]`` or ``Annotated[bytes, Capacity(n)]``.
    Calling the subclass with the field values, as with a dataclass, creates
    the segment; :meth:`attach` opens it from any thread or process. The
    segment is named after the class unless the ``name`` class keyword says
    otherwise; :meth:`create` makes further boxes under explicit names.
    """

    __slots__ = ("__weakref__", "_events", "_finalizer", "_segment", "_watcher")

    _events: SignalGroup | None
    __layout__: ClassVar[Layout]
    __sharedbox_defaults__: ClassVar[dict[str, Any]] = {}
    __lock_timeout__: ClassVar[float] = 5.0
    __sharedbox_name__: ClassVar[str]
    __events_class__: ClassVar[type[SignalGroup]]

    def __init_subclass__(
        cls,
        *,
        name: str | None = None,
        kw_only: bool = False,
        lock_timeout: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        layout = build_layout(cls, kw_only)
        clashes = sorted(RESERVED.intersection(layout.by_name))
        if clashes:
            raise TypeError(
                f"{cls.__qualname__}: field name(s) {', '.join(clashes)} clash with SharedBox methods"
            )
        defaults = dict(cls.__sharedbox_defaults__)
        for spec in layout.fields:
            value = cls.__dict__.get(spec.name, MISSING)
            if value is not MISSING and not isinstance(value, Field):
                spec.encode(value)
                defaults[spec.name] = value
            setattr(cls, spec.name, Field(spec))
        for field_name, value in defaults.items():
            layout.by_name[field_name].encode(value)
        seen_default = False
        for spec in layout.fields:
            if spec.kw_only:
                continue
            if spec.name in defaults:
                seen_default = True
            elif seen_default:
                raise TypeError(
                    f"{cls.__qualname__}: field {spec.name!r} without a default follows a field with one"
                )
        cls.__layout__ = layout
        cls.__sharedbox_defaults__ = defaults
        digest = hashlib.sha256(class_identity(cls).encode()).hexdigest()[:16]
        cls.__sharedbox_name__ = (
            f"sharedbox-{digest}" if name is None else check_name(name)
        )
        if lock_timeout is not None:
            if lock_timeout <= 0:
                raise ValueError("lock_timeout must be positive")
            cls.__lock_timeout__ = lock_timeout
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
        box._segment = Segment.attach(
            cls._layout_name() if name is None else check_name(name),
            cls._layout().schema_hash,
            cls.__lock_timeout__,
        )
        box._watcher = Watcher(box._segment)
        box._events = None
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
        self._finalizer = weakref.finalize(self, release, self._watcher, self._segment)

    def _open(self, name: str, args: tuple[Any, ...], values: dict[str, Any]) -> None:
        cls = type(self)
        layout = cls._layout()
        slots = [spec for spec in layout.fields if not spec.kw_only]
        if len(args) > len(slots):
            raise TypeError(
                f"{cls.__qualname__} takes {len(slots)} positional values, got {len(args)}"
            )
        positional = {spec.name: arg for spec, arg in zip(slots, args)}
        twice = sorted(positional.keys() & values.keys())
        if twice:
            raise TypeError(
                f"{cls.__qualname__} got more than one value for {', '.join(twice)}"
            )
        values = {**positional, **values}
        self._check_names(values)
        merged = {**cls.__sharedbox_defaults__, **values}
        missing = [spec.name for spec in layout.fields if spec.name not in merged]
        if missing:
            raise TypeError(
                f"{cls.__qualname__} is missing value(s) for {', '.join(missing)}"
            )
        encoded = [
            (spec.index, spec.encode(merged[spec.name])) for spec in layout.fields
        ]
        self._segment = Segment.create(
            name,
            [spec.native for spec in layout.fields],
            layout.record_size,
            layout.schema_hash,
            cls.__lock_timeout__,
        )
        self._watcher = Watcher(self._segment)
        self._events = None
        self._track()
        self._segment.write(encoded)

    def _check_names(self, values: dict[str, Any]) -> None:
        unknown = sorted(values.keys() - type(self).__layout__.by_name.keys())
        if unknown:
            raise TypeError(
                f"{type(self).__qualname__} has no field(s) {', '.join(unknown)}"
            )

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
        self._check_names(values)
        by_name = type(self).__layout__.by_name
        self._segment.write(
            [(by_name[n].index, by_name[n].encode(v)) for n, v in values.items()]
        )

    def snapshot(self) -> dict[str, Any]:
        """Every field's value, read at one point in time."""
        raw = self._segment.read_all()
        return {
            spec.name: spec.decode(data)
            for spec, data in zip(type(self).__layout__.fields, raw)
        }

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
        the main thread instead.

        If several writes happen between two checks by the watcher thread,
        only one emission happens, with the latest value; ``old`` is the
        value from the last emission. A write that leaves the value
        unchanged emits nothing. For the first emission of a field, ``old``
        is the value the field held when ``events`` was first accessed.

        A callback that raises is logged. Callbacks connected before it on
        the same signal already ran; callbacks connected after it do not
        run for that write. Other fields still emit normally.
        """
        if self._events is None:
            self._events = self._watcher.events(
                type(self).__events_class__, type(self).__layout__.fields
            )
        return self._events

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

    def close(self) -> None:
        """Detach from the segment; other boxes keep it. Use :meth:`unlink` to remove it."""
        self._finalizer()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(self) -> tuple[Any, tuple[str]]:
        return (type(self).attach, (self.name,))

    def __repr__(self) -> str:
        if self.closed:
            return f"<{type(self).__qualname__} {self.name!r} closed>"
        fields = ", ".join(
            f"{name}={value!r}" for name, value in self.snapshot().items()
        )
        return f"{type(self).__qualname__}({fields})"
