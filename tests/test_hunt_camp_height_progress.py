"""A distant camp remains viable while fresh terrain routes make progress."""
from types import SimpleNamespace as NS

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_navigation import navigate_hunt
from wotlkbuddy.live.control import LiveControlError


def run_camp(progress, *, camp_transit=True, time_budget=1000):
    state=NS(player=NS(in_combat=False))
    where=Vec3(0,0,40)
    destination=Vec3(350,0,40)
    origins=[]
    logs=[]
    moves=iter(progress)
    clock=[0]
    def position(_):
        return 42,NS(map_id=0,player_guid='player',position=where,
                     x=where.x,y=where.y,z=where.z)
    def find_path(map_id,start,end):
        origins.append(start)
        return [start,end]
    def travel(legs,**kwargs):
        nonlocal where
        x=next(moves)
        clock[0]+=10
        where=Vec3(x,0,40)
        return NS(stopped_reason='complete' if x==350 else
                  'Waypoint 8 is on another height/floor')
    result=navigate_hunt(state,destination,camp_transit=camp_transit,
        pid=42,guid='player',quest_id=426,capture=lambda:state,
        driver=NS(emergency_stop_requested=lambda:False),
        nav=NS(find_path=find_path,segment_clear=lambda *a:True),
        world_position=position,guard=lambda _:None,fresh=lambda _:state,
        travel=travel,max_seconds=time_budget,now=lambda:clock[0],
        started=0,log=logs.append)
    return result,origins,logs


def test_camp_replans_beyond_four_failures_when_closer_each_time():
    _,origins,logs=run_camp([50,83,110,198,250,300,350])
    assert [p.x for p in origins]==[0,50,83,110,198,250,300]
    assert sum('destination progress retained' in msg for msg in logs)==6


def test_camp_still_rejects_stagnation():
    with pytest.raises(LiveControlError,match='refusing to retry'):
        run_camp([50,83,83])


def test_camp_respects_overall_time_budget():
    with pytest.raises(LiveControlError,match='time budget exhausted'):
        run_camp([50,83],time_budget=20)


def test_local_route_still_has_one_height_replan():
    with pytest.raises(LiveControlError,match='height/floor'):
        run_camp([50,83],camp_transit=False)
