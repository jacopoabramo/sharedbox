"""Single-operation timings of SharedBox and the standard library's shared memory.

Every contender holds the same record: an int, a float and a string of up
to 32 bytes. ``mp.Value/Array`` takes one lock per value, so its "update two
fields" and "read all" rows take two or three locks one after the other.
Rows under ``split`` time SharedBox's Python encoding and its native call
separately.

    python -m sharedbox.benchmarks.ops -o ops.json
    python -m sharedbox.benchmarks.ops --fast --filter "read*"
"""

import argparse
import atexit
import multiprocessing as mp
import os
import struct
from fnmatch import fnmatchcase
from multiprocessing.shared_memory import ShareableList, SharedMemory
from typing import Annotated, Any

import pyperf

from sharedbox import Capacity, SharedBox

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


OPEN: dict[str, tuple[Any, ...]] = {}


def open_box() -> tuple[Any, ...]:
    name = f"bench-ops-box-{os.getpid()}"
    box = Record.create(name, 0, 0.0, "")
    atexit.register(Record.unlink, name)
    atexit.register(box.close)
    fields = Record.__layout__.by_name
    a, s = fields["a"], fields["s"]
    return box, box._segment, a, s, a.encode(1), s.encode("hello")


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


OPENERS = {
    "box": open_box,
    "shm": open_shm,
    "values": open_values,
    "list": open_list,
    "namespace": open_namespace,
}


def resources(kind: str) -> tuple[Any, ...]:
    """The objects a benchmark of ``kind`` uses, created once per worker process."""
    if kind not in OPEN:
        OPEN[kind] = OPENERS[kind]()
    return OPEN[kind]


BOX = "box, seg, spec_a, spec_s, raw_a, raw_s = resources('box')"
SHM = "buf, lock = resources('shm')"
VALUES = "a, b, s = resources('values')"
LIST = "shared, = resources('list')"
NAMESPACE = "ns, = resources('namespace')"

BENCHMARKS: list[tuple[str, str, str, list[str]]] = [
    ("write int", "SharedBox", BOX, ["box.a = 1"]),
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
    ("encode int", "split", BOX, ["spec_a.encode(1)"]),
    ("decode int", "split", BOX, ["spec_a.decode(raw_a)"]),
    ("native write int", "split", BOX, ["seg.write([(0, raw_a)])"]),
    ("native read int", "split", BOX, ["seg.read(0)"]),
    ("encode str", "split", BOX, ["spec_s.encode('hello')"]),
    ("decode str", "split", BOX, ["spec_s.decode(raw_s)"]),
    ("native write str", "split", BOX, ["seg.write([(2, raw_s)])"]),
    ("native read str", "split", BOX, ["seg.read(2)"]),
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
