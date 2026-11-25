"""Firing recovery must preserve exact target and safe movement boundaries."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_firing import recover_firing_position, plan_firing_route
from wotlkbuddy.live.control import LiveControlError


def context(*, in_combat=False, target_guid='mob', dead=False, actual_pid=42):
    state = NS(player=NS(in_combat=in_combat, target_guid=target_guid),
               combat_target=NS(entry_id=123, dead=dead))
    origin = Vec3(0, 0, 0)
    world = NS(x=0, y=0, z=0, position=origin, map_id=0)
    args = dict(nav=Mock(), driver=Mock(), capture=Mock(),
                world_position=lambda _: (actual_pid, world),
                target_xyz=Mock(return_value=Vec3(20, 0, 0)), pid=42,
                quest_id=364, travel=Mock(), guard=Mock(), fresh=Mock(),
                max_seconds=900, started=0, now=lambda: 0, sleep=Mock(), log=Mock())
    return state, args


def test_active_combat_prevents_firing_reposition():
    state, args = context(in_combat=True)
    assert recover_firing_position(state, 'mob', 123, 0, **args) == (state, False)
    args['target_xyz'].assert_not_called()
    args['travel'].assert_not_called()


@pytest.mark.parametrize('changes', [{'target_guid': 'other'}, {'dead': True}])
def test_changed_or_dead_target_prevents_reposition(changes):
    state, args = context(**changes)
    with pytest.raises(LiveControlError, match='Target changed'):
        recover_firing_position(state, 'mob', 123, 0, **args)
    args['travel'].assert_not_called()


def test_changed_pid_prevents_reposition():
    state, args = context(actual_pid=43)
    with pytest.raises(LiveControlError, match='PID changed'):
        recover_firing_position(state, 'mob', 123, 0, **args)
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


@pytest.mark.parametrize('target', [Vec3(3, 0, 0), Vec3(50, 0, 0), Vec3(20, 0, 7)])
def test_out_of_bounds_target_prevents_reposition(target):
    state, args = context()
    args['target_xyz'].return_value = target
    assert recover_firing_position(state, 'mob', 123, 0, **args) == (state, False)
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


def test_short_firing_route_uses_validated_detour_segment():
    start = Vec3(0, 0, 0)
    player = NS(x=0, y=0, z=0, position=start, map_id=0)
    nav = Mock()
    nav.segment_clear.return_value = True
    nav.find_path.side_effect = lambda _, a, b: (a, b)
    legs, short, distance = plan_firing_route(
        nav=nav, player_pos=player, mob_pos=Vec3(20, 0, 0), dx=20, dy=0,
        old_distance=20, attempt=0, log=Mock())
    assert short is True
    assert distance == pytest.approx(2.8)
    assert legs[-1].distance_to(Vec3(2.8, 0, 0)) < 1e-9
