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
