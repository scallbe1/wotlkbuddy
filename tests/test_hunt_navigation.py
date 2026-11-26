"""Quest-camp navigation boundary tests."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_navigation import navigate_hunt
from wotlkbuddy.live.control import LiveControlError


def context(position, *, actual_pid=42):
    state = NS(player=NS(in_combat=False))
    world = NS(x=position.x, y=position.y, z=position.z, position=position,
               map_id=0, player_guid='character')
    return state, dict(pid=42, guid='character', quest_id=364, capture=Mock(),
                       driver=Mock(), nav=Mock(), world_position=lambda _: (actual_pid, world),
                       guard=Mock(), fresh=Mock(), travel=Mock(), max_seconds=900,
                       now=lambda: 0, started=0, log=Mock())


def test_same_floor_arrival_does_not_start_navigation():
    state, args = context(Vec3(10, 10, 10))
    assert navigate_hunt(state, Vec3(11, 11, 10), **args) is state
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


def test_same_xy_wrong_floor_is_not_arrival():
    state, args = context(Vec3(10, 10, 10))
    with pytest.raises(LiveControlError, match='different heights/floors'):
        navigate_hunt(state, Vec3(10, 10, 20), **args)
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


def test_changed_pid_aborts_before_route_query():
    state, args = context(Vec3(10, 10, 10), actual_pid=43)
    with pytest.raises(LiveControlError, match='PID changed'):
        navigate_hunt(state, Vec3(100, 100, 10), **args)
    args['nav'].find_path.assert_not_called()
    args['travel'].assert_not_called()


def test_loot_displacement_does_not_consume_height_replan_budget():
    start=Vec3(0,0,0)
    target=Vec3(100,0,0)
    initial,args=context(start)
    args['driver']=NS(emergency_stop_requested=lambda:False)
    positions=(start,Vec3(30,0,0),Vec3(35,4,0),Vec3(60,0,0),target)
    states=[NS(player=NS(in_combat=False),position=p) for p in positions]
    def locate(s):
        p=s.position
        return 42,NS(x=p.x,y=p.y,z=p.z,position=p,map_id=0,player_guid='character')
    args['world_position']=locate
    args['nav'].find_path.side_effect=lambda _,a,b:[a,b]
    args['fresh'].side_effect=states[1:]
    args['travel'].side_effect=[NS(stopped_reason=r) for r in (
        'Waypoint 2 is on another height/floor',
        'Threat scan changed player position; fresh Detour replan is required',
        'Threat scan changed player position; fresh Detour replan is required',
        'complete')]
    assert navigate_hunt(states[0],target,**args) is states[-1]
    assert args['nav'].find_path.call_count==4
    assert [c.args[1] for c in args['nav'].find_path.call_args_list]==list(positions[:-1])


def test_displacement_replan_rechecks_map_identity():
    initial,args=context(Vec3(0,0,0))
    args['driver']=NS(emergency_stop_requested=lambda:False)
    target=Vec3(100,0,0)
    initial_world=args['world_position'](initial)[1]
    out=NS(player=NS(in_combat=False))
    wrong=NS(x=5,y=0,z=0,position=Vec3(5,0,0),map_id=1,player_guid='character')
    args['world_position']=lambda s:(42,initial_world if s is initial else wrong)
    args['nav'].find_path.return_value=[initial_world.position,target]
    args['fresh'].return_value=out
    args['travel'].return_value=NS(stopped_reason='fresh Detour replan is required')
    with pytest.raises(LiveControlError,match='map/GUID/PID changed'):
        navigate_hunt(initial,target,**args)
    assert args['nav'].find_path.call_count==1
