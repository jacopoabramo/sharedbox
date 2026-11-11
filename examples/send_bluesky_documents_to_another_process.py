"""The script of the guide "How to send bluesky documents to another process"."""

import multiprocessing as mp
from multiprocessing.synchronize import Event

from bluesky import RunEngine
from bluesky import plan_stubs as bps
from bluesky import plans as bp
from ophyd_async import sim
from ophyd_async.core import init_devices

from sharedbox.bluebox import DocumentReceiver, DocumentSender

CHANNEL = "example-beamline"


# --8<-- [start:receiver]
def watch(channel: str, ready: Event) -> None:
    with DocumentReceiver.attach(channel, mode="lossless") as receiver:
        ready.set()
        for name, doc in receiver:
            if name == "event":
                print("event", doc["seq_num"], sorted(doc["data"]))
            else:
                print(name)


# --8<-- [end:receiver]


def main() -> None:
    # --8<-- [start:devices]
    engine = RunEngine()
    pattern = sim.PatternGenerator()
    with init_devices():
        stage = sim.SimStage(pattern)
        detector = sim.SimPointDetector(pattern)
    engine(bps.mv(detector.acquire_time, 0.01, stage.x.velocity, 100))
    # --8<-- [end:devices]
    # --8<-- [start:sender]
    context = mp.get_context("spawn")
    with DocumentSender.create(CHANNEL) as sender:
        engine.subscribe(sender)
        ready = context.Event()
        receiver = context.Process(target=watch, args=(CHANNEL, ready))
        receiver.start()
        ready.wait()
        engine(bp.scan([detector], stage.x, 0, 1, 3))
    receiver.join()
    # --8<-- [end:sender]


if __name__ == "__main__":
    main()
