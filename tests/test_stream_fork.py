import sys

import pytest
from crossproc import fork

from sharedbox import SharedStream, StreamClosedError, WouldBlock

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork"),
    pytest.mark.filterwarnings("ignore:This process .* is multi-threaded"),
]


def test_an_inherited_reader_is_closed_in_the_child_and_works_in_the_parent(
    unique_name: str,
) -> None:
    """Raise StreamClosedError for an inherited reader in a forked child, and keep the parent's reader working."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()

        def child() -> None:
            with pytest.raises(StreamClosedError):
                reader.receive_nowait()
            reader.close()
            own = SharedStream.attach(int, unique_name).reader()
            with pytest.raises(WouldBlock):
                own.receive_nowait()
            own.close()

        process = fork(child)
        process.join(60)
        assert process.exitcode == 0
        assert not reader.closed
        sender.send(1)
        assert reader.receive(timeout=5) == 1


def test_an_inherited_sender_is_closed_in_the_child_and_keeps_the_stream_open(
    unique_name: str,
) -> None:
    """Raise StreamClosedError for an inherited sender in a forked child, and leave the stream open for the parent's sender and reader."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()

        def child() -> None:
            with pytest.raises(StreamClosedError):
                sender.send_nowait(9)
            sender.close()

        process = fork(child)
        process.join(60)
        assert process.exitcode == 0
        sender.send(2)
        assert reader.receive(timeout=5) == 2


def test_an_inherited_stream_opens_a_reader_in_the_child(unique_name: str) -> None:
    """Open a reader in a forked child from the stream it inherited, and receive what the parent sent."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        sender = stream.sender()
        sender.send(1)

        def child() -> None:
            own = stream.reader(start="oldest")
            assert own.receive(timeout=5) == 1
            own.close()

        process = fork(child)
        process.join(60)
        assert process.exitcode == 0
