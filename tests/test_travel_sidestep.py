"""Measured sidestep recovery keeps original budgets and movement checks."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel_sidestep import SidestepState, recover_sidestep


def position(x=0, y=0, z=0, facing=0):
    return NS(x=x, y=y, z=z, facing_rad=facing, position=Vec3(x, y, z))


def context(**overrides):
    args = dict(can_traverse=Mock(return_value=False), max_recoveries=4,
                recovery_locations=[], guard=Mock(), pulse=Mock(),
                checked=Mock(), notify=Mock())
    args.update(overrides)
    return args


def test_no_mesh_clearance_service_means_no_movement():
    state = SidestepState(object(), position(), 0, 0, 0)
    args = context(can_traverse=None)
    assert recover_sidestep(state, **args) is False
    args['pulse'].assert_not_called()
    assert state.recoveries == 0


def test_recovery_budget_is_enforced_before_movement():
    state = SidestepState(object(), position(), 0, 4, 0)
    args = context()
    with pytest.raises(LiveControlError, match='budget exhausted'):
        recover_sidestep(state, **args)
    args['pulse'].assert_not_called()


def test_mesh_blocked_on_both_sides_never_pulses():
    state = SidestepState(object(), position(), 0, 0, 0)
    args = context()
    assert recover_sidestep(state, **args) is False
    assert state.recoveries == 1
    assert args['can_traverse'].call_count == 2
    assert args['recovery_locations'] == [Vec3(0, 0, 0)]
    args['pulse'].assert_not_called()


def test_sidestep_disagreement_preserves_measured_position_and_count():
    state = SidestepState(object(), position(), 0, 0, 0)
    observed = object()
    moved = position(4, 0, 0)
    args = context(can_traverse=Mock(return_value=True),
                   checked=Mock(return_value=(observed, moved)))
    with pytest.raises(LiveControlError, match='Unexpected movement during obstacle sidestep'):
        recover_sidestep(state, **args)
    assert state.current is observed and state.world is moved
    assert state.strafes == 1 and state.recoveries == 1
    args['pulse'].assert_called_once_with('strafe-right', .18)
