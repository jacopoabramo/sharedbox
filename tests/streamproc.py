from sharedbox import SharedStream


def send_ints(name: str, count: int) -> None:
    """Send the integers below `count` on the stream `name`, then close the sender."""
    stream = SharedStream.attach(int, name)
    sender = stream.sender()
    for i in range(count):
        sender.send(i)
    sender.close()
    stream.close()


def exit_while_delivering(name: str) -> None:
    """Connect a callback to a reader of the stream `name` and return, leaving delivery running."""
    stream = SharedStream.attach(int, name)
    reader = stream.reader()
    reader.events.received.connect(lambda item, position: None)
    stream.sender().send(1)
