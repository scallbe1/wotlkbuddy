"""Continuous movement preserves input release and passive observation bounds."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.travel_forward import smooth_forward


def position(x=0, y=0, z=0, facing=0, map_id=0, guid='player'):
    return NS(x=x, y=y, z=z, position=Vec3(x, y, z),
              facing_rad=facing, map_id=map_id, player_guid=guid)


def context(**overrides):
    clock = [0.]
    args = dict(guard=Mock(), fast_observe=Mock(return_value=(position(), False)),
                expected_map_id=0, guid='player', now=lambda: clock[0],
                travel_input=Mock(), arrival_yards=1.8, right_sign=1,
                turn_rate=2, sleep=lambda duration: clock.__setitem__(0, clock[0]+duration))
    args.update(overrides)
    return args


@pytest.mark.parametrize('observation', [(position(), True), (position(x=2), False)])
def test_preflight_hazard_or_displacement_does_not_press_forward(observation):
    args = context(fast_observe=Mock(return_value=observation))
    assert smooth_forward(position(), Vec3(10, 0, 0), object(), **args) is None
    args['travel_input'].key_down.assert_not_called()


def test_hazard_during_burst_releases_forward():
    args = context(fast_observe=Mock(side_effect=[(position(), False), (position(), True)]))
    _, interrupted, steering = smooth_forward(position(), Vec3(10, 0, 0), object(), **args)
    assert interrupted and steering == 0
    args['travel_input'].key_up.assert_called_once_with(_MOVEMENT_SCANCODES['forward'])


def test_changed_identity_during_burst_releases_forward():
    args = context(fast_observe=Mock(side_effect=[
        (position(), False), (position(guid='other'), False)]))
    with pytest.raises(LiveControlError, match='changed map or identity'):
        smooth_forward(position(), Vec3(10, 0, 0), object(), **args)
    args['travel_input'].key_up.assert_called_once_with(_MOVEMENT_SCANCODES['forward'])


def test_observation_failure_releases_steering_and_forward():
    args = context(fast_observe=Mock(side_effect=[
        (position(), False), (position(facing=-.2), False), OSError('read failed')]))
    with pytest.raises(LiveControlError, match='Fast movement observation failed'):
        smooth_forward(position(), Vec3(10, 0, 0), object(), **args)
    assert [c.args[0] for c in args['travel_input'].key_down.call_args_list] == [
        _MOVEMENT_SCANCODES['forward'], _MOVEMENT_SCANCODES['turn-right']]
    assert [c.args[0] for c in args['travel_input'].key_up.call_args_list] == [
        _MOVEMENT_SCANCODES['turn-right'], _MOVEMENT_SCANCODES['forward']]


def test_forward_burst_is_bounded_even_when_position_does_not_change():
    args = context()
    elapsed, interrupted, steering = smooth_forward(position(), Vec3(10, 0, 0), object(), **args)
    assert 2 <= elapsed <= 2.03
    assert not interrupted and steering == 0
    args['travel_input'].key_up.assert_called_once_with(_MOVEMENT_SCANCODES['forward'])
