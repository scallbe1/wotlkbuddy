from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_height_staging import alternate_floor_points,recover_height_staging
from wotlkbuddy.horde.hunt_retry import HuntRouteState,select_spawn_goal
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.live.control import LiveControlError


def test_height_recovery_preserves_only_required_spawn():
    origin=Vec3(2846.58,1022.25,117.52)
    center=Vec3(2824,1045,111.32)
    where=NS(position=origin,map_id=0)
    goal=NS(x=center.x,y=center.y,z=center.z,position=center,entry=1656)
    state=NS(player=NS(in_combat=False))
    nav=NS(nearest_point=lambda m,p:p,find_path=lambda m,a,b:[a,b])
    routes=HuntRouteState(staging_visited=[Vec3(origin.x,origin.y,111.32)])
    navigate=Mock(side_effect=[LiveControlError('height/floor'),state])
    assert recover_height_staging(goal,routes=routes,driver=NS(),nav=nav,
        capture=lambda:state,guard=lambda _:None,world_position=lambda _:(42,where),
        pid=42,map_id=0,navigate=navigate,now=lambda:0,
        clear_remaining_aggro=Mock(),log=Mock())
    assert navigate.call_count==2
    first,second=[call.args[1] for call in navigate.call_args_list]
    assert first.distance_to(second)>=7
    assert not routes.failed_navigation
    assert routes.recently_visited[(2824,1045,111)]==0


def test_height_search_rejects_partial_routes():
    where=NS(position=Vec3(0,0,6),map_id=0)
    nav=NS(nearest_point=lambda m,p:p,find_path=lambda m,a,b:[a,Vec3(1,0,6)])
    assert not alternate_floor_points(nav,where,Vec3(30,0,0),[],[],lambda:None)


def test_exhausted_height_search_is_retryable_not_fatal():
    routes=HuntRouteState(refresh_rounds=2,failed_navigation={
        (2824,1045,111):LiveControlError('height/floor')})
    with pytest.raises(EncounterRouteUnavailable,match='refresh live terrain'):
        select_spawn_goal(object(),routes=routes,nearby=[],spec=NS(entry=1656),
            allowed_sources=(1656,),expected=1656,collect_items=True,
            rest=Mock(),sleep=Mock(),unhittable_guids=set(),unhittable_positions=[],log=Mock())
