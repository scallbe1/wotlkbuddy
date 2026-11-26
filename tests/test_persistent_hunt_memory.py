from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_terrain import HuntTerrain, attach_hunt_memory, retreat_candidates
from wotlkbuddy.horde.hunt_routes import remember_pack


def test_phase_drivers_share_character_history_but_not_other_characters():
    memory={}
    first=NS()
    second=NS()
    other=NS()
    attach_hunt_memory(first,'A',memory)
    first.finished_corpses.add('corpse')
    first.seen_kill_events.add((1,'corpse'))
    first.hunt_terrain.remember(NS(map_id=0,position=Vec3(1,2,3)))
    remember_pack(first,[Vec3(4,5,6)],10)
    attach_hunt_memory(second,'A',memory)
    attach_hunt_memory(other,'B',memory)
    assert second.finished_corpses=={'corpse'}
    assert second.seen_kill_events=={(1,'corpse')}
    assert second.hunt_terrain.anchors==[(0,Vec3(1,2,3))]
    assert second.dangerous_hunt_positions==[(Vec3(4,5,6),10)]
    assert not other.finished_corpses


def test_fallback_retreat_searches_complete_navmesh_routes():
    where=NS(map_id=0,position=Vec3(0,0,0),z=0.)
    state=NS(player=NS(in_combat=False),resources=NS(race_token='undead'))
    threat=NS(position=Vec3(12,0,0),entry=1544,faction=14)
    driver=NS(awareness=NS(read=lambda *args:[threat]),
              emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    nav=NS(nearest_point=lambda m,p:p,find_path=lambda m,a,b:(a,b))
    points=list(retreat_candidates([],state,where,driver,nav,lambda _:None))
    assert points
    assert points[0][1].x<0
    assert all(p.distance_to(threat.position)>=24 for _,p in points)


def test_retreat_uses_fallback_when_no_history_exists():
    where=NS(map_id=0,position=Vec3(0,0,0),z=0.)
    state=NS(player=NS(in_combat=False),resources=NS(race_token='undead'))
    driver=NS(awareness=NS(read=lambda *args:[]),emergency_stop_requested=lambda:False,
              wow_is_foreground=lambda:True)
    nav=NS(nearest_point=lambda m,p:p,find_path=lambda m,a,b:(a,b))
    routes=NS(route_replans=0,force_spawn_search=False)
    terrain=HuntTerrain(failures=3)
    destinations=[]
    assert terrain.retreat(state,routes=routes,world_position=lambda _:(1,where),
        navigate=lambda s,p:destinations.append(p),nav=nav,driver=driver,
        now=lambda:10,log=lambda _:None)
    assert destinations[0].distance_to(where.position)>=18
    assert routes.force_spawn_search
