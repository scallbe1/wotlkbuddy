"""Shared navigation state is visible to observations and recovery immediately."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.travel_state import TravelSessionState
from wotlkbuddy.live.travel_waypoint import execute_waypoint
from wotlkbuddy.live.travel_sidestep import recover_sidestep


def position(x=0):
    return NS(x=x, y=0, z=0, facing_rad=0, position=Vec3(x, 0, 0))


def test_observation_callback_tracks_replacement_route_and_cursor():
    state = TravelSessionState(object(), position(), tuple(Vec3(x, 0, 0) for x in range(10)))
    observe = state.upcoming_legs
    assert observe() == tuple(Vec3(x, 0, 0) for x in range(1, 7))
    state.waypoints = (Vec3(0, 0, 0), Vec3(0, 5, 0), Vec3(5, 5, 0))
    state.next_waypoint_index = 2
    assert observe() == (Vec3(5, 5, 0),)


def test_waypoint_and_sidestep_share_measured_counters_without_copying():
    observed = (object(), position())
    state = TravelSessionState(*observed, (Vec3(0, 0, 0), Vec3(10, 0, 0)))

    def recover(reason):
        assert reason == 'no forward progress'
        assert state.moves == 4
        return recover_sidestep(
            state, can_traverse=Mock(return_value=False), max_recoveries=4,
            recovery_locations=[], guard=Mock(), pulse=Mock(),
            checked=Mock(), notify=Mock()) or True

    assert execute_waypoint(
        state, Vec3(10, 0, 0), index=1, waypoint_count=1, arrival_yards=1.8,
        guard=Mock(), turn_rate=2, right_sign=1, pulse=Mock(return_value=.2),
        sample=Mock(return_value=observed), checked=Mock(return_value=observed),
        cautious=False, on_threat=None, continuous_forward=False, ghost_only=False,
        fast_observe=None, smooth_forward=Mock(), recover_collision=recover,
        replan_path=None, recover_around_wall=Mock(), breadcrumbs=[Vec3(0, 0, 0)])
    assert state.moves == 4 and state.recoveries == 1


def test_new_sessions_do_not_inherit_previous_route_counters():
    route = (Vec3(0, 0, 0), Vec3(10, 0, 0))
    first = TravelSessionState(object(), position(), route)
    first.moves = 8
    first.recoveries = 2
    first.next_waypoint_index = 2
    second = TravelSessionState(object(), position(), route)
    assert (second.moves, second.recoveries, second.next_waypoint_index) == (0, 0, 1)
