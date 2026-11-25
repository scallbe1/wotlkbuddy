"""Backtrack recovery preserves measured movement and route safety."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel_backtrack import BacktrackState, backtrack_and_replan, recover_lateral


def position(x=0, y=0, z=0):
    return NS(x=x, y=y, z=z, facing_rad=0, position=Vec3(x, y, z))


def state():
    return BacktrackState(object(), position(), 0, 0, 0,
                          (Vec3(0, 0, 0), Vec3(10, 0, 0)), False, 0, 1)


def context(**overrides):
    args = dict(replan_path=Mock(), can_traverse=Mock(return_value=False),
                max_recoveries=4, guard=Mock(), recovery_locations=[],
                blocked_waypoints=[], blocked_edges=[], breadcrumbs=[Vec3(0, 0, 0)],
                notify=Mock(), right_sign=1, turn_rate=2, pulse=Mock(), sample=Mock(),
                checked=Mock(), destination=Vec3(10, 0, 0), arrival_yards=1.8,
                validate_nav_path=Mock(), route_reuses_collision=Mock(),
                alternate_wall_route=Mock(return_value=None))
    args.update(overrides)
    return args


def test_disabled_replan_is_no_input():
    s = state()
    args = context(replan_path=None)
    assert backtrack_and_replan(s, 'wall', **args) is False
    assert s.recoveries == 0
    args['pulse'].assert_not_called()


def test_repeated_lateral_recovery_checks_other_side_first():
    s = state()
    s.recoveries = 2
    mesh = Mock(return_value=False)
    pulse = Mock()
    recover_lateral(s, repeated=True, origin=s.world.position, guard=Mock(),
                    can_traverse=mesh, pulse=pulse, sample=Mock(), checked=Mock(),
                    notify=Mock())
    assert mesh.call_count == 2
    assert mesh.call_args_list[0].args[1].y > 0
    pulse.assert_not_called()


def test_replan_failure_preserves_collision_evidence_and_budget():
    s = state()
    args = context(replan_path=Mock(side_effect=RuntimeError('no route')))
    with pytest.raises(LiveControlError, match='Backtracked but fresh Detour route unavailable'):
        backtrack_and_replan(s, 'wall', **args)
    assert s.recoveries == 1
    assert args['blocked_waypoints'] == [Vec3(10, 0, 0)]
    assert args['blocked_edges'] == [(Vec3(0, 0, 0), 0)]
    args['pulse'].assert_not_called()


def test_new_route_is_set_before_final_threat_scan():
    s = state()
    route = (Vec3(0, 0, 0), Vec3(0, 4, 0), Vec3(10, 0, 0))
    def observe(**kwargs):
        assert kwargs == {'force_scan': True}
        assert s.waypoints == route and s.next_waypoint_index == 1
        return s.current, s.world
    args = context(replan_path=Mock(return_value=route),
                   validate_nav_path=Mock(return_value=route),
                   route_reuses_collision=Mock(return_value=False), checked=observe)
    assert backtrack_and_replan(s, 'wall', **args) is True
    assert s.cautious
