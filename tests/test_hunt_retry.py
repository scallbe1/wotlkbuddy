"""Route retry state preserves safety bounds and persistent failures."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_retry import HuntRouteState, handle_spawn_failure, select_spawn_goal
from wotlkbuddy.live.control import LiveControlError


def failure_context(**overrides):
    world = NS(map_id=0, x=10, y=20, z=30)
    args = dict(routes=HuntRouteState(), key=(1, 2, 3), allowed_sources=(123,),
                capture=Mock(return_value=NS(player=NS(in_combat=False))),
                guard=Mock(), world_position=Mock(return_value=(42, world)),
                pid=42, world=world, log=Mock())
    args.update(overrides)
    return args


def test_wrong_floor_excluded_without_counting_collision():
    args = failure_context()
    error = LiveControlError('height/floor')
    handle_spawn_failure(error, **args)
    assert args['routes'].failed_navigation == {(1, 2, 3): error}
    assert args['routes'].recently_visited[(1, 2, 3)] == 2
    assert args['routes'].navigation_mismatches == 0


def test_unique_required_target_unsafe_pack_remains_retryable():
    args = failure_context()
    handle_spawn_failure(LiveControlError('unsafe encounter: pack'), **args)
    routes = args['routes']
    assert routes.force_spawn_search and routes.route_replans == 1
    assert routes.recently_visited[(1, 2, 3)] == 0
    assert not routes.failed_navigation


def test_second_collision_stops_route_retries():
    args = failure_context(routes=HuntRouteState(navigation_mismatches=1))
    with pytest.raises(LiveControlError, match='Two off-axis navigation failures'):
        handle_spawn_failure(LiveControlError('no forward progress'), **args)
    assert args['routes'].navigation_mismatches == 2


def test_nonrecoverable_failure_is_propagated_before_observation():
    args = failure_context()
    error = LiveControlError('focus lost')
    with pytest.raises(LiveControlError) as caught:
        handle_spawn_failure(error, **args)
    assert caught.value is error
    args['capture'].assert_not_called()


def test_combat_blocks_alternate_route():
    args = failure_context(capture=Mock(return_value=NS(player=NS(in_combat=True))))
    with pytest.raises(LiveControlError, match='refusing alternate routing'):
        handle_spawn_failure(LiveControlError('no forward progress'), **args)
    assert not args['routes'].failed_navigation


def test_patrol_refresh_does_not_clear_failed_navigation():
    error = LiveControlError('height/floor')
    routes = HuntRouteState(recently_visited={(1, 2, 3): 2},
                            failed_navigation={(1, 2, 3): error})
    guids, positions = {'mob'}, [Vec3(1, 2, 3)]
    rest, sleep = Mock(), Mock()
    assert select_spawn_goal(
        object(), routes=routes, nearby=[], spec=NS(entry=123),
        allowed_sources=(123,), expected=123, collect_items=False,
        rest=rest, sleep=sleep, unhittable_guids=guids,
        unhittable_positions=positions, log=Mock()) is None
    assert routes.refresh_rounds == 1
    assert not routes.recently_visited and not guids and not positions
    assert routes.failed_navigation == {(1, 2, 3): error}
    sleep.assert_called_once_with(8)


def test_combat_during_failed_route_is_resolved_without_excluding_objective():
    fighting=NS(player=NS(in_combat=True))
    safe=NS(player=NS(in_combat=False))
    defend=Mock(return_value=safe)
    routes=HuntRouteState(recently_visited={(1,2,3):1})
    args=failure_context(routes=routes,capture=Mock(return_value=fighting),
                         clear_remaining_aggro=defend)
    handle_spawn_failure(LiveControlError('Unsafe encounter: could not acquire exact discovered hostile GUID'),**args)
    defend.assert_called_once_with(fighting)
    assert not routes.failed_navigation
    assert not routes.recently_visited
    assert routes.route_replans==0


def test_off_axis_combat_is_recoverable_at_session_boundary():
    from wotlkbuddy.horde.aggro_recovery import recoverable_aggro_error
    assert recoverable_aggro_error(LiveControlError('Combat began during off-axis navigation; refusing alternate routing'))
    assert not recoverable_aggro_error(LiveControlError('Cannot verify map/PID after interrupted navigation'))
