from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.threat_scan import entrance_scan_scope, reacquired_corridor_candidate
from wotlkbuddy.horde.entrance_model import EntranceMemory, move_entrance, next_stage
from wotlkbuddy.horde.hunt_terrain import HuntTerrain
from wotlkbuddy.live.control import LiveControlError


def test_entrance_only_scans_immediate_walking_corridor():
    path=[Vec3(5,0,0),Vec3(20,0,0)]
    assert entrance_scan_scope(NS(entrance_approach_guid='boss'),path,35,18)==(path[:1],15.,8.)
    assert entrance_scan_scope(NS(),path,35,18)==(path,35,18)


def test_same_name_alternate_must_be_an_independently_discovered_corridor_enemy():
    candidates=[(10.,'original',1537),(12.,'other',1537)]
    assert reacquired_corridor_candidate(
        NS(player=NS(target_guid='other'),combat_target=NS(entry_id=1537)),candidates)==candidates[1]
    assert reacquired_corridor_candidate(
        NS(player=NS(target_guid='unknown'),combat_target=NS(entry_id=1537)),candidates) is None


def test_can_walk_inside_old_nine_yard_exclusion():
    start=Vec3(0,0,0)
    target=Vec3(8,0,0)
    assert next_stage((start,Vec3(4,0,0),target),target)==Vec3(4,0,0)


def test_rejected_combat_does_not_poison_physical_route_memory():
    start=Vec3(0,0,0)
    target=Vec3(20,0,0)
    where=NS(map_id=0,position=start,x=0,y=0,z=0)
    memory=EntranceMemory(0,1662,'perrine',target)
    terrain=HuntTerrain(entrance=memory)
    state=NS(player=NS(guid='player',in_combat=False))
    routes=NS(force_spawn_search=True)
    driver=NS()
    nav=NS(find_path=lambda m,a,b:(a,b))
    awareness=NS(capture=lambda:state,_guard=lambda *_:None)
    def navigate(*args,**kwargs):
        assert driver.entrance_approach_guid=='perrine'
        raise LiveControlError('Unsafe encounter: nearby linked group')
    assert move_entrance(terrain,memory,state,where,routes=routes,
        world_position=lambda s:(1,where),navigate=navigate,nav=nav,
        driver=driver,awareness=awareness,log=lambda _:None)
    assert not memory.blocked_edges
    assert driver.entrance_approach_guid is None
