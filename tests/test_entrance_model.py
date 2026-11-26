from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.entrance_model import (
    EntranceMemory, choose_route, next_stage, ready_position, move_entrance)
from wotlkbuddy.horde.hunt_terrain import HuntTerrain


class RampMesh:
    def find_path(self,map_id,start,end):
        # Once on the ramp's top, the route is direct.
        if start.z>=4:
            return (start,end)
        return (start,Vec3(-8,0,0),Vec3(-8,12,4),Vec3(4,12,6),end)

    def segment_clear(self,map_id,start,end):
        return start.z>=4


def test_ramp_route_can_begin_away_from_enemy():
    where=NS(map_id=0,position=Vec3(0,0,0))
    memory=EntranceMemory(0,1662,'perrine',Vec3(12,0,6))
    path=choose_route(RampMesh(),where,memory,lambda:None)
    stage=next_stage(path,memory.target)
    assert stage.x<0
    assert stage.distance_to(memory.target)>where.position.distance_to(memory.target)


def test_remembered_route_suffix_wins_over_new_shortest_path():
    class UnexpectedMesh:
        def find_path(self,*args):
            raise AssertionError('must retain verified entrance suffix')
    start=Vec3(-8,0,0)
    memory=EntranceMemory(0,1662,'perrine',Vec3(12,0,6),
        pending_path=(start,Vec3(-8,5,2),Vec3(-8,10,4),Vec3(12,0,6)))
    assert choose_route(UnexpectedMesh(),NS(map_id=0,position=start),memory,
                        lambda:None)==memory.pending_path


def test_wrong_floor_and_already_offered_positions_are_not_ready():
    memory=EntranceMemory(0,1662,'perrine',Vec3(12,0,6))
    assert not ready_position(RampMesh(),NS(map_id=0,position=Vec3(0,0,1)),memory)
    where=NS(map_id=0,position=Vec3(0,0,5))
    assert ready_position(RampMesh(),where,memory)
    memory.offered.append(where.position)
    assert not ready_position(RampMesh(),where,memory)


def test_measured_failed_climb_records_blocked_corridor():
    start=Vec3(0,0,0)
    where=NS(map_id=0,position=start,x=0,y=0,z=0)
    memory=EntranceMemory(0,1662,'perrine',Vec3(12,0,6))
    terrain=HuntTerrain(entrance=memory)
    state=NS(player=NS(guid='player',in_combat=False))
    routes=NS(force_spawn_search=True)
    def navigate(s,point,*,approach_path):
        assert approach_path[-1]==point
        return NS(player=s.player,where=NS(map_id=0,
            position=Vec3(point.x,point.y,point.z-2),x=point.x,y=point.y,z=point.z-2))
    awareness=NS(capture=lambda:state,_guard=lambda *_:None)
    assert move_entrance(terrain,memory,state,where,routes=routes,
        world_position=lambda s:(1,getattr(s,'where',where)),navigate=navigate,
        nav=RampMesh(),driver=NS(),awareness=awareness,log=lambda _:None)
    assert memory.blocked_edges
    assert not memory.reached
    assert not memory.pending_path
