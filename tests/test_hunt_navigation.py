"""Quest-camp navigation boundary tests."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_navigation import navigate_hunt
from wotlkbuddy.live.control import LiveControlError


def context(position, *, actual_pid=42):
    state = NS(player=NS(in_combat=False))
    world = NS(x=position.x, y=position.y, z=position.z, position=position,
               map_id=0, player_guid='character')
    return state, dict(pid=42, guid='character', quest_id=364, capture=Mock(),
                       driver=Mock(), nav=Mock(), world_position=lambda _: (actual_pid, world),
                       guard=Mock(), fresh=Mock(), travel=Mock(), max_seconds=900,
                       now=lambda: 0, started=0, log=Mock())


def test_same_floor_arrival_does_not_start_navigation():
    state, args = context(Vec3(10, 10, 10))
    assert navigate_hunt(state, Vec3(11, 11, 10), **args) is state
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


def test_same_xy_wrong_floor_is_not_arrival():
    state, args = context(Vec3(10, 10, 10))
    with pytest.raises(LiveControlError, match='different heights/floors'):
        navigate_hunt(state, Vec3(10, 10, 20), **args)
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


def test_changed_pid_aborts_before_route_query():
    state, args = context(Vec3(10, 10, 10), actual_pid=43)
    with pytest.raises(LiveControlError, match='PID changed'):
        navigate_hunt(state, Vec3(100, 100, 10), **args)
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()
