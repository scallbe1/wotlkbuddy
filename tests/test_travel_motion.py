"""Movement timing and turn calibration remain bounded and identity-specific."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from weakref import WeakKeyDictionary

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.travel_motion import movement_pulse, calibrate_turn


def test_frozen_clock_still_bounds_key_hold():
    input_owner, sleep = Mock(), Mock()
    assert movement_pulse('forward', .1, guard=Mock(), now=lambda: 0,
                          sleep=sleep, travel_input=input_owner) == 0
    durations = [c.args[0] for c in sleep.call_args_list]
    assert sum(durations) == pytest.approx(.1)
    assert max(durations) <= .04
    input_owner.key_up.assert_called_once_with(_MOVEMENT_SCANCODES['forward'])


def test_interrupted_pulse_releases_key():
    input_owner = Mock()
    guard = Mock(side_effect=[None, LiveControlError('focus lost')])
    with pytest.raises(LiveControlError, match='focus lost'):
        movement_pulse('forward', .1, guard=guard, now=lambda: 0,
                       sleep=Mock(), travel_input=input_owner)
    input_owner.key_up.assert_called_once_with(_MOVEMENT_SCANCODES['forward'])


def test_slow_sleep_cannot_extend_pulse():
    clock = [0.]
    def sleep(duration):
        clock[0] += .2
    input_owner = Mock()
    held = movement_pulse('forward', .1, guard=Mock(), now=lambda: clock[0],
                          sleep=sleep, travel_input=input_owner)
    assert held == pytest.approx(.2)
    input_owner.key_up.assert_called_once_with(_MOVEMENT_SCANCODES['forward'])


class Keyboard:
    pass


def position(facing=0, x=0, map_id=0):
    return NS(facing_rad=facing, position=Vec3(x, 0, 0), map_id=map_id)


def calibration_context(**overrides):
    args = dict(cache=WeakKeyDictionary(), keyboard=Keyboard(), guid='player',
                current=object(), world=position(), turns=0, pulse=Mock(),
                checked=Mock(return_value=(object(), position(.3))), notify=Mock())
    args.update(overrides)
    return args


def test_calibration_reused_only_for_same_character_and_map():
    args = calibration_context()
    result = calibrate_turn(**args)
    assert result[2:] == (1, 1, pytest.approx(2.5))
    args['pulse'].assert_called_once_with('turn-right', .12)
    args['pulse'].reset_mock()
    assert calibrate_turn(**args)[2:] == (0, 1, pytest.approx(2.5))
    args['pulse'].assert_not_called()
    args['guid'] = 'different'
    calibrate_turn(**args)
    args['pulse'].assert_called_once_with('turn-right', .12)


@pytest.mark.parametrize('observed, message', [
    (position(.01), 'plausible facing'),
    (position(.3, x=2), 'translated unexpectedly')])
def test_invalid_calibration_is_not_cached(observed, message):
    args = calibration_context(checked=Mock(return_value=(object(), observed)))
    with pytest.raises(LiveControlError, match=message):
        calibrate_turn(**args)
    assert not args['cache']
