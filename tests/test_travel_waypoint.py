"""Waypoint execution preserves measured movement and recovery boundaries."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel_waypoint import WaypointState, execute_waypoint


def position(x=0, y=0, z=0, facing=0):
    return NS(x=x, y=y, z=z, facing_rad=facing, position=Vec3(x, y, z))


def context(**overrides):
    args = dict(index=1, waypoint_count=1, arrival_yards=1.8,
                guard=Mock(), turn_rate=2., right_sign=1, pulse=Mock(return_value=.2),
                sample=Mock(), checked=Mock(), cautious=False, on_threat=None,
                continuous_forward=False, ghost_only=False, fast_observe=None,
                smooth_forward=Mock(), recover_collision=Mock(return_value=False),
                replan_path=None, recover_around_wall=Mock(return_value=False),
                breadcrumbs=[Vec3(0, 0, 0)])
    args.update(overrides)
    return args


def test_arrived_waypoint_issues_no_input():
    state = WaypointState(object(), position(), 0, 0)
    args = context()
    assert execute_waypoint(state, Vec3(1, 0, 0), **args) is False
    args['pulse'].assert_not_called()


def test_turn_disagreement_records_turn_before_stopping():
    state = WaypointState(object(), position(facing=1), 0, 0)
    observed = (object(), position(facing=1))
    args = context(sample=Mock(return_value=observed), checked=Mock(return_value=observed))
    with pytest.raises(LiveControlError, match='did not rotate'):
        execute_waypoint(state, Vec3(10, 0, 0), **args)
    assert state.turns == 1


def test_forward_jump_preserves_measured_state():
    state = WaypointState(object(), position(), 0, 0)
    observed = (object(), position(x=20))
    args = context(sample=Mock(return_value=observed), checked=Mock(return_value=observed))
    with pytest.raises(LiveControlError, match='Implausible world jump'):
        execute_waypoint(state, Vec3(10, 0, 0), **args)
    assert state.moves == 1 and state.world is observed[1]


def test_four_stalled_pulses_request_recovery():
    observed = (object(), position())
    state = WaypointState(*observed, 0, 0)
    args = context(sample=Mock(return_value=observed), checked=Mock(return_value=observed),
                   recover_collision=Mock(return_value=True))
    assert execute_waypoint(state, Vec3(10, 0, 0), **args) is True
    assert state.moves == 4
    args['recover_collision'].assert_called_once_with('no forward progress')


def test_successful_leg_records_breadcrumb():
    state = WaypointState(object(), position(), 0, 0)
    observed = (object(), position(x=3))
    args = context(sample=Mock(return_value=observed), checked=Mock(return_value=observed))
    assert execute_waypoint(state, Vec3(4, 0, 0), **args) is False
    assert state.moves == 1
    assert args['breadcrumbs'][-1] == Vec3(3, 0, 0)
