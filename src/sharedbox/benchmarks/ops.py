"""Single-operation timings of SharedBox and the standard library's shared memory.

The contenders that compare against the standard library hold the same
record: an int, a float and a string of up to 32 bytes. Rows for a datetime,
a record, a list, a set, a dict and an array time SharedBox alone; they have no
standard-library counterpart. `mp.Value/Array` takes one lock per value, so
its "update two fields" and "read all" rows take two or three locks one after
the other.
The row `watched write int` writes while another process has a watcher open.
Rows under `split` isolate the native segment's typed `set`/`get`,
which convert the Python value in the native module, against the raw
`_write`/`_read` calls that move already-encoded bytes.

    python -m sharedbox.benchmarks.ops -o ops.json
    python -m sharedbox.benchmarks.ops --fast --filter "read*"
"""

import argparse
import atexit
import datetime
import multiprocessing as mp
import os
import struct
from dataclasses import dataclass
from fnmatch import fnmatchcase
from multiprocessing.shared_memory import ShareableList, SharedMemory
from multiprocessing.synchronize import Event
from typing import Annotated, Any

import numpy as np
import pyperf

from sharedbox import BoundedList, BoundedSet, Capacity, DType, Shape, SharedBox

INT = struct.Struct("<q")
FLOAT = struct.Struct("<d")
STR = struct.Struct("<I32s")
PAIR = struct.Struct("<qd")
RECORD = struct.Struct("<qdI32s")
LOCKED_VALUES = "mp.Value/Array (lock per value)"


class Record(SharedBox):
    a: int
    b: float
    s: Annotated[str, Capacity(32)]


class Holder(SharedBox):
    record: Record | None = None


@dataclass(frozen=True)
class Quad:
    a: int
    b: float
    c: bool
    d: int


OPEN: dict[str, tuple[Any, ...]] = {}


def open_box() -> tuple[Any, ...]:
    name = f"bench-ops-box-{os.getpid()}"
    box = Record.create(name, 0, 0.0, "")
    atexit.register(Record.unlink, name)
    atexit.register(box.close)
    return box, box._segment, INT.pack(1)


def ignore(new: int, old: int) -> None:
    """Receive a change of `a` and do nothing with it."""


def watch(name: str, ready: Event, stop: Event) -> None:
    """Watch field `a` of the box `name` until `stop` is set."""
    box = Record.attach(name)
    box.events.a.connect(ignore)
    ready.set()
    stop.wait()
    box.close()


def open_watched() -> tuple[Any, ...]:
    name = f"bench-ops-watched-{os.getpid()}"
    box = Record.create(name, 0, 0.0, "")
    ctx = mp.get_context("spawn")
    ready, stop = ctx.Event(), ctx.Event()
    child = ctx.Process(target=watch, args=(name, ready, stop))
    child.start()

    def stop_child() -> None:
        stop.set()
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join()

    atexit.register(Record.unlink, name)
    atexit.register(box.close)
    atexit.register(stop_child)
    if not ready.wait(30):
        raise TimeoutError("the watching process did not start")
    return (box,)


def open_ref() -> tuple[Any, ...]:
    name = f"bench-ops-ref-{os.getpid()}"
    record = Record.create(f"{name}-record", 0, 0.0, "")
    holder = Holder.create(name, record)
    # The row times a read that finds the box it attached before.
    _ = holder.record
    atexit.register(Record.unlink, record.name)
    atexit.register(Holder.unlink, name)
    atexit.register(record.close)
    atexit.register(holder.close)
    return (holder,)


def open_shm() -> tuple[Any, ...]:
    shm = SharedMemory(f"bench-ops-shm-{os.getpid()}", create=True, size=RECORD.size)
    atexit.register(shm.unlink)
    atexit.register(shm.close)
    return shm.buf, mp.Lock()


def open_values() -> tuple[Any, ...]:
    return mp.Value("q"), mp.Value("d"), mp.Array("c", 32)


def open_list() -> tuple[Any, ...]:
    shared = ShareableList[Any](
        [0, 0.0, " " * 32], name=f"bench-ops-list-{os.getpid()}"
    )
    atexit.register(shared.shm.unlink)
    atexit.register(shared.shm.close)
    return (shared,)


def open_namespace() -> tuple[Any, ...]:
    manager = mp.Manager()
    atexit.register(manager.shutdown)
    ns = manager.Namespace()
    ns.a, ns.b, ns.s = 0, 0.0, ""
    return (ns,)


class Typed(SharedBox):
    when: datetime.datetime
    quad: Quad
    floats: Annotated[list[float], Capacity(16)]
    l1k: Annotated[list[float], Capacity(1024)]
    l64k: Annotated[list[float], Capacity(65536)]
    b16: Annotated[BoundedList[float], Capacity(16)]
    b1k: Annotated[BoundedList[float], Capacity(1024)]
    b64k: Annotated[BoundedList[float], Capacity(65536)]
    s16: Annotated[set[int], Capacity(16)]
    s1k: Annotated[set[int], Capacity(1024)]
    s64k: Annotated[set[int], Capacity(65536)]
    bs16: Annotated[BoundedSet[int], Capacity(16)]
    bs1k: Annotated[BoundedSet[int], Capacity(1024)]
    bs64k: Annotated[BoundedSet[int], Capacity(65536)]
    d16: Annotated[dict[int, float], Capacity(16)]
    d1k: Annotated[dict[int, float], Capacity(1024)]
    d64k: Annotated[dict[int, float], Capacity(65536)]
    image: Annotated[np.ndarray, Shape(512, 512), DType("float32")]


WHEN = datetime.datetime(2026, 10, 2, 9, 30, tzinfo=datetime.UTC)
QUAD = Quad(1, 1.5, True, 2)
FLOATS = [0.5] * 16
F1K = [0.5] * 1024
F64K = [0.5] * 65536
B16 = BoundedList[float](FLOATS, capacity=16)
B1K = BoundedList[float](F1K, capacity=1024)
B64K = BoundedList[float](F64K, capacity=65536)
S16 = set(range(16))
S1K = set(range(1024))
S64K = set(range(65536))
BS16 = BoundedSet[int](S16, capacity=16)
BS1K = BoundedSet[int](S1K, capacity=1024)
BS64K = BoundedSet[int](S64K, capacity=65536)
D16 = dict.fromkeys(range(16), 0.5)
D1K = dict.fromkeys(range(1024), 0.5)
D64K = dict.fromkeys(range(65536), 0.5)
IMAGE = np.ones((512, 512), np.float32)
OUT = np.empty_like(IMAGE)


def fill_image(box: Typed) -> None:
    with box.writing("image") as view:
        np.copyto(view, IMAGE)


def open_typed() -> tuple[Any, ...]:
    name = f"bench-ops-typed-{os.getpid()}"
    box = Typed.create(
        name,
        WHEN,
        QUAD,
        FLOATS,
        F1K,
        F64K,
        B16,
        B1K,
        B64K,
        S16,
        S1K,
        S64K,
        BS16,
        BS1K,
        BS64K,
        D16,
        D1K,
        D64K,
        IMAGE,
    )
    atexit.register(Typed.unlink, name)
    atexit.register(box.close)
    return (box,)


OPENERS = {
    "box": open_box,
    "watched": open_watched,
    "ref": open_ref,
    "shm": open_shm,
    "values": open_values,
    "list": open_list,
    "namespace": open_namespace,
    "typed": open_typed,
}


def resources(kind: str) -> tuple[Any, ...]:
    """The objects a benchmark of `kind` uses, created once per worker process."""
    if kind not in OPEN:
        OPEN[kind] = OPENERS[kind]()
    return OPEN[kind]


BOX = "box, seg, raw_a = resources('box')"
WATCHED = "box, = resources('watched')"
REF = "holder, = resources('ref')"
SHM = "buf, lock = resources('shm')"
VALUES = "a, b, s = resources('values')"
LIST = "shared, = resources('list')"
NAMESPACE = "ns, = resources('namespace')"
TYPED = "typed, = resources('typed')"

BENCHMARKS: list[tuple[str, str, str, list[str]]] = [
    ("write int", "SharedBox", BOX, ["box.a = 1"]),
    ("watched write int", "SharedBox", WATCHED, ["box.a = 1"]),
    ("write int", "SharedMemory+struct", SHM, ["INT.pack_into(buf, 0, 1)"]),
    (
        "write int",
        "SharedMemory+struct+Lock",
        SHM,
        ["with lock: INT.pack_into(buf, 0, 1)"],
    ),
    ("write int", "mp.Value/Array", VALUES, ["a.value = 1"]),
    ("write int", "ShareableList", LIST, ["shared[0] = 1"]),
    ("write int", "Manager().Namespace()", NAMESPACE, ["ns.a = 1"]),
    ("read int", "SharedBox", BOX, ["box.a"]),
    ("read int", "SharedMemory+struct", SHM, ["INT.unpack_from(buf, 0)[0]"]),
    (
        "read int",
        "SharedMemory+struct+Lock",
        SHM,
        ["with lock: INT.unpack_from(buf, 0)[0]"],
    ),
    ("read int", "mp.Value/Array", VALUES, ["a.value"]),
    ("read int", "ShareableList", LIST, ["shared[0]"]),
    ("read int", "Manager().Namespace()", NAMESPACE, ["ns.a"]),
    ("write float", "SharedBox", BOX, ["box.b = 1.5"]),
    ("write float", "SharedMemory+struct", SHM, ["FLOAT.pack_into(buf, 8, 1.5)"]),
    (
        "write float",
        "SharedMemory+struct+Lock",
        SHM,
        ["with lock: FLOAT.pack_into(buf, 8, 1.5)"],
    ),
    ("write float", "mp.Value/Array", VALUES, ["b.value = 1.5"]),
    ("write float", "ShareableList", LIST, ["shared[1] = 1.5"]),
    ("write float", "Manager().Namespace()", NAMESPACE, ["ns.b = 1.5"]),
    ("read float", "SharedBox", BOX, ["box.b"]),
    ("read float", "SharedMemory+struct", SHM, ["FLOAT.unpack_from(buf, 8)[0]"]),
    (
        "read float",
        "SharedMemory+struct+Lock",
        SHM,
        ["with lock: FLOAT.unpack_from(buf, 8)[0]"],
    ),
    ("read float", "mp.Value/Array", VALUES, ["b.value"]),
    ("read float", "ShareableList", LIST, ["shared[1]"]),
    ("read float", "Manager().Namespace()", NAMESPACE, ["ns.b"]),
    ("write str", "SharedBox", BOX, ["box.s = 'hello'"]),
    (
        "write str",
        "SharedMemory+struct",
        SHM,
        ["data = 'hello'.encode()", "STR.pack_into(buf, 16, len(data), data)"],
    ),
    (
        "write str",
        "SharedMemory+struct+Lock",
        SHM,
        [
            "data = 'hello'.encode()",
            "with lock: STR.pack_into(buf, 16, len(data), data)",
        ],
    ),
    ("write str", "mp.Value/Array", VALUES, ["s.value = 'hello'.encode()"]),
    ("write str", "ShareableList", LIST, ["shared[2] = 'hello'"]),
    ("write str", "Manager().Namespace()", NAMESPACE, ["ns.s = 'hello'"]),
    ("read str", "SharedBox", BOX, ["box.s"]),
    (
        "read str",
        "SharedMemory+struct",
        SHM,
        ["n, raw = STR.unpack_from(buf, 16)", "raw[:n].decode()"],
    ),
    (
        "read str",
        "SharedMemory+struct+Lock",
        SHM,
        ["with lock: n, raw = STR.unpack_from(buf, 16)", "raw[:n].decode()"],
    ),
    ("read str", "mp.Value/Array", VALUES, ["s.value.decode()"]),
    ("read str", "ShareableList", LIST, ["shared[2]"]),
    ("read str", "Manager().Namespace()", NAMESPACE, ["ns.s"]),
    ("update two fields", "SharedBox", BOX, ["box.update(a=1, b=1.5)"]),
    (
        "update two fields",
        "SharedMemory+struct",
        SHM,
        ["PAIR.pack_into(buf, 0, 1, 1.5)"],
    ),
    (
        "update two fields",
        "SharedMemory+struct+Lock",
        SHM,
        ["with lock: PAIR.pack_into(buf, 0, 1, 1.5)"],
    ),
    ("update two fields", LOCKED_VALUES, VALUES, ["a.value = 1", "b.value = 1.5"]),
    ("update two fields", "ShareableList", LIST, ["shared[0] = 1", "shared[1] = 1.5"]),
    (
        "update two fields",
        "Manager().Namespace()",
        NAMESPACE,
        ["ns.a = 1", "ns.b = 1.5"],
    ),
    ("read all", "SharedBox", BOX, ["box.snapshot()"]),
    ("read ref", "SharedBox", REF, ["holder.record"]),
    (
        "read all",
        "SharedMemory+struct",
        SHM,
        [
            "a, b, n, raw = RECORD.unpack_from(buf)",
            "{'a': a, 'b': b, 's': raw[:n].decode()}",
        ],
    ),
    (
        "read all",
        "SharedMemory+struct+Lock",
        SHM,
        [
            "with lock: a, b, n, raw = RECORD.unpack_from(buf)",
            "{'a': a, 'b': b, 's': raw[:n].decode()}",
        ],
    ),
    (
        "read all",
        LOCKED_VALUES,
        VALUES,
        ["{'a': a.value, 'b': b.value, 's': s.value.decode()}"],
    ),
    (
        "read all",
        "ShareableList",
        LIST,
        ["{'a': shared[0], 'b': shared[1], 's': shared[2]}"],
    ),
    (
        "read all",
        "Manager().Namespace()",
        NAMESPACE,
        ["{'a': ns.a, 'b': ns.b, 's': ns.s}"],
    ),
    ("native set int", "split", BOX, ["seg.set([(0, 1)])"]),
    ("native get int", "split", BOX, ["seg.get(0)"]),
    ("raw write int", "split", BOX, ["seg._write([(0, raw_a)])"]),
    ("raw read int", "split", BOX, ["seg._read(0)"]),
    ("native set str", "split", BOX, ["seg.set([(2, 'hello')])"]),
    ("native get str", "split", BOX, ["seg.get(2)"]),
    ("native versions", "split", BOX, ["seg.versions()"]),
    ("write datetime", "SharedBox", TYPED, ["typed.when = WHEN"]),
    ("read datetime", "SharedBox", TYPED, ["typed.when"]),
    ("write record", "SharedBox", TYPED, ["typed.quad = QUAD"]),
    ("read record", "SharedBox", TYPED, ["typed.quad"]),
    ("write+read record", "SharedBox", TYPED, ["typed.quad = QUAD", "typed.quad"]),
    ("write list16", "SharedBox", TYPED, ["typed.floats = FLOATS"]),
    ("read list16", "SharedBox", TYPED, ["typed.floats"]),
    ("write list1k", "SharedBox", TYPED, ["typed.l1k = F1K"]),
    ("read list1k", "SharedBox", TYPED, ["typed.l1k"]),
    ("write list64k", "SharedBox", TYPED, ["typed.l64k = F64K"]),
    ("read list64k", "SharedBox", TYPED, ["typed.l64k"]),
    ("iterate list16", "SharedBox", TYPED, ["for _ in typed.floats: pass"]),
    ("index list16", "SharedBox", TYPED, ["typed.floats[8]"]),
    ("iterate list1k", "SharedBox", TYPED, ["for _ in typed.l1k: pass"]),
    ("index list1k", "SharedBox", TYPED, ["typed.l1k[512]"]),
    ("iterate list64k", "SharedBox", TYPED, ["for _ in typed.l64k: pass"]),
    ("index list64k", "SharedBox", TYPED, ["typed.l64k[32768]"]),
    ("write list16", "BoundedList", TYPED, ["typed.b16 = B16"]),
    ("read list16", "BoundedList", TYPED, ["typed.b16"]),
    ("write list1k", "BoundedList", TYPED, ["typed.b1k = B1K"]),
    ("read list1k", "BoundedList", TYPED, ["typed.b1k"]),
    ("write list64k", "BoundedList", TYPED, ["typed.b64k = B64K"]),
    ("read list64k", "BoundedList", TYPED, ["typed.b64k"]),
    ("iterate list16", "BoundedList", TYPED, ["for _ in typed.b16: pass"]),
    ("index list16", "BoundedList", TYPED, ["typed.b16[8]"]),
    ("iterate list1k", "BoundedList", TYPED, ["for _ in typed.b1k: pass"]),
    ("index list1k", "BoundedList", TYPED, ["typed.b1k[512]"]),
    ("iterate list64k", "BoundedList", TYPED, ["for _ in typed.b64k: pass"]),
    ("index list64k", "BoundedList", TYPED, ["typed.b64k[32768]"]),
    ("write set16", "SharedBox", TYPED, ["typed.s16 = S16"]),
    ("read set16", "SharedBox", TYPED, ["typed.s16"]),
    ("in set16", "SharedBox", TYPED, ["8 in typed.s16"]),
    ("iterate set16", "SharedBox", TYPED, ["for _ in typed.s16: pass"]),
    ("write set1k", "SharedBox", TYPED, ["typed.s1k = S1K"]),
    ("read set1k", "SharedBox", TYPED, ["typed.s1k"]),
    ("in set1k", "SharedBox", TYPED, ["512 in typed.s1k"]),
    ("iterate set1k", "SharedBox", TYPED, ["for _ in typed.s1k: pass"]),
    ("write set64k", "SharedBox", TYPED, ["typed.s64k = S64K"]),
    ("read set64k", "SharedBox", TYPED, ["typed.s64k"]),
    ("in set64k", "SharedBox", TYPED, ["32768 in typed.s64k"]),
    ("iterate set64k", "SharedBox", TYPED, ["for _ in typed.s64k: pass"]),
    ("write set16", "BoundedSet", TYPED, ["typed.bs16 = BS16"]),
    ("read set16", "BoundedSet", TYPED, ["typed.bs16"]),
    ("in set16", "BoundedSet", TYPED, ["8 in typed.bs16"]),
    ("iterate set16", "BoundedSet", TYPED, ["for _ in typed.bs16: pass"]),
    ("write set1k", "BoundedSet", TYPED, ["typed.bs1k = BS1K"]),
    ("read set1k", "BoundedSet", TYPED, ["typed.bs1k"]),
    ("in set1k", "BoundedSet", TYPED, ["512 in typed.bs1k"]),
    ("iterate set1k", "BoundedSet", TYPED, ["for _ in typed.bs1k: pass"]),
    ("write set64k", "BoundedSet", TYPED, ["typed.bs64k = BS64K"]),
    ("read set64k", "BoundedSet", TYPED, ["typed.bs64k"]),
    ("in set64k", "BoundedSet", TYPED, ["32768 in typed.bs64k"]),
    ("iterate set64k", "BoundedSet", TYPED, ["for _ in typed.bs64k: pass"]),
    ("write dict16", "SharedBox", TYPED, ["typed.d16 = D16"]),
    ("read dict16", "SharedBox", TYPED, ["typed.d16"]),
    ("in dict16", "SharedBox", TYPED, ["8 in typed.d16"]),
    ("iterate dict16", "SharedBox", TYPED, ["for _ in typed.d16: pass"]),
    ("write dict1k", "SharedBox", TYPED, ["typed.d1k = D1K"]),
    ("read dict1k", "SharedBox", TYPED, ["typed.d1k"]),
    ("in dict1k", "SharedBox", TYPED, ["512 in typed.d1k"]),
    ("iterate dict1k", "SharedBox", TYPED, ["for _ in typed.d1k: pass"]),
    ("write dict64k", "SharedBox", TYPED, ["typed.d64k = D64K"]),
    ("read dict64k", "SharedBox", TYPED, ["typed.d64k"]),
    ("in dict64k", "SharedBox", TYPED, ["32768 in typed.d64k"]),
    ("iterate dict64k", "SharedBox", TYPED, ["for _ in typed.d64k: pass"]),
    ("write array 1 MiB", "SharedBox", TYPED, ["typed.image = IMAGE"]),
    ("read array 1 MiB", "SharedBox", TYPED, ["typed.image"]),
    ("read_into array 1 MiB", "SharedBox", TYPED, ["typed.read_into('image', OUT)"]),
    ("writing array 1 MiB", "SharedBox", TYPED, ["fill_image(typed)"]),
]


def forward_filter(cmd: list[str], args: argparse.Namespace) -> None:
    if args.filter:
        cmd.extend(("--filter", args.filter))


def main() -> None:
    runner = pyperf.Runner(
        # Workers start the module by name, so they import the same sharedbox
        # whether it runs from a checkout or from an installed wheel.
        program_args=("-m", "sharedbox.benchmarks.ops"),
        add_cmdline_args=forward_filter,
    )
    runner.argparser.add_argument(
        "--filter",
        metavar="PATTERN",
        help="run only benchmarks whose operation/contender name matches this "
        "glob pattern",
    )
    pattern = runner.parse_args().filter
    for operation, contender, setup, stmt in BENCHMARKS:
        name = f"{operation}/{contender}"
        if pattern is None or fnmatchcase(name, pattern):
            runner.timeit(name, stmt, setup, globals=globals())


if __name__ == "__main__":
    main()
