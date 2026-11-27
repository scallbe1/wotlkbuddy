"""GameObject travel resumes after encounter loot moves the player."""
from types import SimpleNamespace as NS
import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.gameobject_quests import navigate_object_destination
from wotlkbuddy.live.control import LiveControlError

REASON='Threat scan changed player position; fresh Detour replan is required'


@pytest.mark.parametrize('reason',[REASON,
    'Defensive combat displaced Mage away from original navmesh path; fresh Detour route required',
    'Escape pressed or WoW lost focus during world navigation'])
def test_gameobject_travel_reanchors_only_known_displacement(reason):
    state=NS(player=NS(guid='owner',dead=False,in_combat=False),truncated=False,
             quests=[NS(quest_id=5481)])
    location=[Vec3(0,0,0)]
    paths=[]
    destination=Vec3(50,0,0)
    def position(s):
        p=location[0]
        return 77,NS(position=p,x=p.x,y=p.y,z=p.z,map_id=0)
    def find_path(map_id,start,end):
        paths.append((start,end))
        return [start,end]
    def travel(legs,**kwargs):
        if len(paths)==1:
            location[0]=Vec3(20,4,0)
            return NS(stopped_reason=reason)
        location[0]=destination
        return NS(stopped_reason='complete')
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    args=dict(pid=77,world_position=position,capture=lambda:state,guard=lambda s:None,
              driver=driver,nav=NS(find_path=find_path,segment_clear=lambda *a:True),
              travel=travel,goal=NS(quest_id=5481),started=0,max_seconds=900,
              now=lambda:0,sleep=lambda _:None,log=lambda _:None,fresh=lambda s:s)
    if reason.startswith('Escape'):
        with pytest.raises(LiveControlError,match='Escape'):
            navigate_object_destination(state,destination,**args)
        assert len(paths)==1
    else:
        assert navigate_object_destination(state,destination,**args) is state
        assert paths==[(Vec3(0,0,0),destination),(Vec3(20,4,0),destination)]


def test_distant_pumpkin_hotspot_not_mistaken_for_exhausted():
    from wotlkbuddy.horde.gameobject_quests import next_object_hotspot
    origin=Vec3(2428.47,357.53,33.88)
    spots=(Vec3(2259.64,1430.03,33.45),Vec3(2265.59,1354.08,33.33))
    assert next_object_hotspot(spots,set(),origin)==(1,spots[1])
    assert next_object_hotspot(spots,{1},origin)==(0,spots[0])
    assert next_object_hotspot(spots,{0,1},origin) is None


def test_distant_object_route_uses_bounded_segments():
    state=NS(player=NS(guid='owner',dead=False,in_combat=False),truncated=False,
             quests=[NS(quest_id=365)])
    location=[Vec3(0,0,0)]
    destination=Vec3(1050,0,0)
    lengths=[]
    def position(s):
        p=location[0]
        return 77,NS(position=p,x=p.x,y=p.y,z=p.z,map_id=0)
    def travel(legs,**kwargs):
        assert legs[0]==location[0]
        assert kwargs['expected_quest_id']==365
        lengths.append(sum(a.distance_to(b) for a,b in zip(legs,legs[1:])))
        location[0]=legs[-1]
        return NS(stopped_reason='complete')
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    result=navigate_object_destination(state,destination,pid=77,world_position=position,
        capture=lambda:state,guard=lambda s:None,driver=driver,
        nav=NS(find_path=lambda m,a,b:[a,b],segment_clear=lambda *a:True),
        travel=travel,goal=NS(quest_id=365),started=0,max_seconds=900,
        now=lambda:0,sleep=lambda _:None,log=lambda _:None,fresh=lambda s:s)
    assert result is state and location[0]==destination
    assert len(lengths)==3 and max(lengths)<=450.01
