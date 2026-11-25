"""Offline regression coverage for rejected pulls during NPC travel."""
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import (detour_around_encounters,
    path_avoids_pack, unsafe_route_encounter)


class OpenMesh:
    def find_path(self,map_id,start,end):
        return (start,end)
    def nearest_point(self,map_id,point):
        return point


def test_rejected_pull_is_recoverable_but_focus_loss_is_not():
    assert unsafe_route_encounter('Unsafe encounter: prefer isolated enemy; nearby linked group')
    assert unsafe_route_encounter('Possible pack behind cave bend')
    assert not unsafe_route_encounter('Escape pressed or WoW lost focus')
    assert not unsafe_route_encounter('Character, map, telemetry, or alive state changed')


def test_vendor_detour_does_not_repeat_blocked_direct_path():
    start=Vec3(0,0,0)
    destination=Vec3(100,0,0)
    hazard=Vec3(35,0,0)
    assert not path_avoids_pack((start,destination),(hazard,),radius=20)
    route=detour_around_encounters(OpenMesh(),0,start,destination,(hazard,))
    assert route is not None
    assert route[0]==start and route[-1]==destination
    assert path_avoids_pack(route,(hazard,),radius=20)
    assert any(abs(p.y)>20 for p in route)


def test_retreat_away_from_nearby_pack_is_valid():
    start=Vec3(0,0,0)
    destination=Vec3(-50,0,0)
    hazard=Vec3(10,0,0)
    route=detour_around_encounters(OpenMesh(),0,start,destination,(hazard,))
    assert route is not None
    assert path_avoids_pack(route,(hazard,),radius=20)


def test_blocked_mesh_does_not_invent_shortcut():
    class BlockedMesh(OpenMesh):
        def nearest_point(self,map_id,point):
            return None
    assert detour_around_encounters(BlockedMesh(),0,Vec3(0,0,0),
        Vec3(100,0,0),(Vec3(35,0,0),)) is None


def test_detour_search_propagates_aggro_immediately():
    import pytest
    from wotlkbuddy.horde.hunt_routes import RouteCombatInterrupted
    calls=[]
    def guard():
        calls.append(True)
        if len(calls)==2:
            raise RouteCombatInterrupted('Aggro/combat detected during encounter detour')
    with pytest.raises(RouteCombatInterrupted):
        detour_around_encounters(OpenMesh(),0,Vec3(0,0,0),
            Vec3(100,0,0),(Vec3(35,0,0),),guard=guard)
    assert len(calls)==2


def test_detour_wait_error_enters_live_aggro_recovery():
    from wotlkbuddy.horde.aggro_recovery import recoverable_aggro_error
    from wotlkbuddy.live.control import LiveControlError
    assert recoverable_aggro_error(LiveControlError(
        'Aggro/combat detected during encounter detour wait'))
    assert not recoverable_aggro_error(LiveControlError('Escape pressed or WoW lost focus'))
