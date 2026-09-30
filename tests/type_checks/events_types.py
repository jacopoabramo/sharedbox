from typing import assert_type

from psygnal import SignalInstance

from sharedbox import BoxEvents, SharedBox


class Motor(SharedBox):
    position: int = 0


class Stage(SharedBox):
    motor: Motor | None = None


def follow(stage: Stage) -> None:
    assert_type(stage.events, BoxEvents)
    assert_type(stage.events.follow("motor"), BoxEvents)
    assert_type(stage.events.follow("motor").position, SignalInstance)
    assert_type(stage.events.unfollow("motor"), None)
    assert_type(stage.events.unfollow(), None)
    assert_type(stage.events.follow(), None)
    assert_type(stage.events.nested, SignalInstance)
