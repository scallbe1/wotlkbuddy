"""Incomplete Detour paths are candidate failures, not successful arrivals."""
from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_retry import HuntRouteState, handle_spawn_failure
from wotlkbuddy.horde.hunt_routes import recoverable_hunt_failure
from wotlkbuddy.horde.hunt_search import search_spawn_candidates
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.control import LiveControlError


def test_partial_route_rejects_candidate_without_consuming_collision_budget():
    routes = HuntRouteState()
    state = NS(player=NS(in_combat=False))
    position = NS(map_id=0, x=0., y=0., z=0.)
    exc = LiveControlError('Navmesh route does not reach the quest destination (partial path)')
    assert recoverable_hunt_failure(exc)
    for key in ((10, 0, 0), (20, 0, 0), (30, 0, 0)):
        handle_spawn_failure(exc, routes=routes, key=key, allowed_sources=(1543,1544),
            capture=lambda:state, guard=lambda _:None,
            world_position=lambda _:(1,position), pid=1, world=position, log=lambda _:None)
    assert len(routes.failed_navigation)==3
    assert routes.navigation_mismatches==0
    assert not recoverable_hunt_failure('Escape pressed or WoW lost focus')


def test_failed_local_candidates_do_not_hide_distant_camp():
    local=Spawn(1543,0,10,0,0)
    distant=Spawn(1544,0,492,0,0)
    result=search_spawn_candidates(spawns=None,
        world=NS(map_id=0,position=Vec3(0,0,0)),
        spec=NS(hotspots=(distant.position,)),allowed_sources=(1543,1544),
        expected=1543,refresh_rounds=0,unhittable_positions=(),
        failed_navigation={(10,0,0):LiveControlError('partial path')},
        reference_search_points=(),collect_items=True,
        nearest_objective_spawns=lambda *a,**kw:[local],
        distant_objective_spawns=lambda *a,**kw:[distant] if kw['entry']==1544 else [],
        log=lambda _:None)
    assert result.remote_camp
    assert result.candidates==[distant]
