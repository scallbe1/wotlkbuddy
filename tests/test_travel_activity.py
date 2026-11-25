"""Movement ownership at the world-route combat handoff."""
from unittest.mock import Mock

import pytest

from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel_activity import TravelInput


def test_handoff_requires_every_route_key_released():
    keyboard = Mock()
    route = TravelInput(keyboard)
    callback = Mock()
    route.key_down(17)
    route.key_down(32)
    route.key_up(17)
    with pytest.raises(LiveControlError, match='keys are held'):
        route.interrupt('defense', callback)
    callback.assert_not_called()
    route.key_up(32)
    assert route.interrupt('defense', callback) is callback.return_value


@pytest.mark.parametrize('activity', ['defense', 'threat_scan'])
def test_callback_owns_handoff_and_route_cannot_move_or_reenter(activity):
    keyboard = Mock()
    route = TravelInput(keyboard)
    def callback(value):
        assert route.activity == activity
        with pytest.raises(LiveControlError, match='input attempted'):
            route.key_down(17)
        with pytest.raises(LiveControlError, match='another interrupt'):
            route.interrupt('defense', Mock())
        return value
    assert route.interrupt(activity, callback, 42) == 42
    keyboard.key_down.assert_not_called()
    assert route.activity == 'navigation'
    route.key_down(17)
    keyboard.key_down.assert_called_once_with(17)


def test_callback_exception_restores_route_activity():
    route = TravelInput(Mock())
    error = LiveControlError('combat failed')
    with pytest.raises(LiveControlError) as caught:
        route.interrupt('defense', Mock(side_effect=error))
    assert caught.value is error
    assert route.activity == 'navigation'


def test_failed_release_blocks_callback():
    keyboard = Mock()
    route = TravelInput(keyboard)
    route.key_down(17)
    keyboard.key_up.side_effect = OSError('release failed')
    with pytest.raises(OSError):
        route.key_up(17)
    callback = Mock()
    with pytest.raises(LiveControlError, match='keys are held'):
        route.interrupt('threat_scan', callback)
    callback.assert_not_called()


def test_invalid_activity_is_rejected():
    route = TravelInput(Mock())
    callback = Mock()
    with pytest.raises(ValueError):
        route.interrupt('vendor', callback)
    callback.assert_not_called()
