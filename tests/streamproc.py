from sharedbox import SharedStream


def send_ints(name: str, count: int) -> None:
    """Send the integers below `count` on the stream `name`, then close the sender."""
    stream = SharedStream.attach(int, name)
    sender = stream.sender()
    for i in range(count):
        sender.send(i)
    sender.close()
    stream.close()
