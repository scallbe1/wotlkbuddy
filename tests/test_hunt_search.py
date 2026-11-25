"""Direct tests of profile-constrained search and persistent route exclusion."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.horde.hunt_search import search_spawn_candidates, next_unvisited_spawn


def search(**overrides):
    args = dict(spawns=None, world=NS(map_id=0, position=Vec3(0, 0, 0)),
                spec=NS(entry=123, hotspots=(Vec3(100, 0, 0),)),
                allowed_sources=(123,), expected=123, refresh_rounds=0,
                unhittable_positions=[], failed_navigation={},
                reference_search_points=(), collect_items=False,
                nearest_objective_spawns=Mock(return_value=[]),
                distant_objective_spawns=Mock(return_value=[]), log=Mock())
    args.update(overrides)
    return search_spawn_candidates(**args)


def test_local_candidates_sorted_and_exclude_failed_routes_and_blocked_positions():
    nearest = Mock(return_value=[Spawn(123, 0, x, 0, 0) for x in (80, 10, 20, 40)])
    result = search(nearest_objective_spawns=nearest,
                    failed_navigation={(20, 0, 0): RuntimeError('floor')},
                    unhittable_positions=[Vec3(10, 0, 0)])
    assert [p.x for p in result.candidates] == [40, 80]
    assert not result.reference_only and not result.remote_camp
    assert nearest.call_args.kwargs['radius'] == 360


def test_refresh_expands_local_radius():
    nearest = Mock(return_value=[Spawn(123, 0, 100, 0, 0)])
    search(nearest_objective_spawns=nearest, refresh_rounds=1)
    assert nearest.call_args.kwargs['radius'] == 600


def test_distant_database_camp_is_not_a_reference():
    far = Spawn(123, 0, 500, 0, 0)
    result = search(distant_objective_spawns=Mock(return_value=[far]))
    assert result.candidates == [far]
    assert result.remote_camp and not result.reference_only


def test_reference_only_accepts_matching_source_map_and_hotspot():
    valid = Spawn(123, 0, 100, 0, 0)
    result = search(reference_search_points=(
        valid, Spawn(124, 0, 100, 0, 0), Spawn(123, 1, 100, 0, 0),
        Spawn(123, 0, 300, 0, 0)))
    assert result.candidates == [valid]
    assert result.reference_only and not result.remote_camp


def test_rejected_reference_does_not_fall_back_to_xml_hotspot():
    result = search(reference_search_points=(Spawn(124, 0, 100, 0, 0),))
    assert not result.candidates


def test_xml_hotspot_search_only_for_kill_objectives():
    result = search()
    assert result.reference_only and result.remote_camp
    assert result.candidates == [Spawn(123, 0, 100, 0, 0)]
    assert not search(collect_items=True).candidates


def test_spawn_attempt_limit():
    a, b = Spawn(123, 0, 10, 0, 0), Spawn(123, 0, 20, 0, 0)
    assert next_unvisited_spawn([a, b], {(10, 0, 0): 2}) == b
    assert next_unvisited_spawn([a, b], {(10, 0, 0): 2, (20, 0, 0): 2}) is None
