from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.world_position import NearbyCreature
from wotlkbuddy.horde.awareness import ObjectAwareness
from wotlkbuddy.horde.threat_scan import scan_and_clear_hostiles
from wotlkbuddy.live.control import LiveControlError
from test_horde_combat_v019 import snap

G1='0xF13000079500058F'
G2='0xF130000795000606'
G3='0xF1300005F500057B'
ENTRY=1941

def setup(rows, targets=()):
    s=snap()
    state=[replace(s,player=replace(s.player,target_guid='',target_name=''))]
    queue=iter(targets)
    commands=[]
    def capture():
        return state[0]
    def target(guid):
        old=state[0]
        row=next((m for m in rows if m.guid==guid),None)
        t=replace(s.combat_target,entry_id=row.entry if row else 0,
                  health=137,reaction=2,attackable=True,dead=False)
        state[0]=replace(old,sequence=old.sequence+1,combat_target=t,
             player=replace(old.player,target_guid=guid,target_name='Rot Hide Graverobber'))
    def chat(c):
        commands.append(c)
        if c=='/targetenemy':
            target(next(queue,state[0].player.target_guid))
    def select(name):
        commands.append('/targetexact '+name)
        target(G1)
        return True
    driver=NS(chat=chat,target_objective_name=select,wow_is_foreground=lambda:True,
              emergency_stop_requested=lambda:False)
    awareness=ObjectAwareness(read=lambda s,p:rows,capture=capture,driver=driver,
                              sleep=lambda _:None)
    where=NS(position=Vec3(0,0,0),map_id=0,z=0,player_guid=s.player.guid)
    return state,commands,awareness,where,driver

def test_empty_spatial_scan_requires_no_tab_targeting():
    state,commands,awareness,where,_=setup(())
    _,enemies,unknown=awareness.survey(state[0],where)
    assert enemies==unknown==()
    assert commands==[]

def test_known_name_acquires_discovered_guid_without_tab():
    mob=NearbyCreature(G1,ENTRY,Vec3(32,0,0),137,14)
    state,commands,awareness,where,_=setup((mob,))
    awareness.names[ENTRY]='Rot Hide Graverobber'
    _,enemies,unknown=awareness.survey(state[0],where)
    assert [e.guid for e in enemies]==[G1] and not unknown
    assert commands==['/targetexact Rot Hide Graverobber']

def test_spatial_discovery_survives_repeated_tab_guid():
    rows=(NearbyCreature(G1,ENTRY,Vec3(32,0,0),137,14),
          NearbyCreature(G2,ENTRY,Vec3(33,2,0),137,14))
    state,commands,awareness,where,_=setup(rows,(G1,G1,G2))
    _,enemies,unknown=awareness.survey(state[0],where)
    assert {e.guid for e in enemies}=={G1,G2} and not unknown
    assert commands==['/targetenemy']*3

def test_unclassified_object_is_retained_not_guessed_hostile():
    rows=(NearbyCreature(G1,ENTRY,Vec3(32,0,0),137,14),
          NearbyCreature(G3,1525,Vec3(24,2,0),120,14))
    state,commands,awareness,where,_=setup(rows,(G1,))
    _,enemies,unknown=awareness.survey(state[0],where)
    assert [e.guid for e in enemies]==[G1]
    assert [m.guid for m in unknown]==[G3]
    assert len(commands)==16

def test_unclassified_route_hazard_stops_before_advance():
    rows=(NearbyCreature(G3,1525,Vec3(23,0,0),120,14),)
    state,commands,awareness,where,driver=setup(rows)
    driver.awareness=awareness
    with pytest.raises(LiveControlError,match='unclassified nearby object'):
        scan_and_clear_hostiles(initial=state[0],player_position=where,
            path_ahead=(Vec3(30,0,0),),capture=lambda:state[0],driver=driver,
            target_position=lambda s:Vec3(23,0,0),sleep=lambda _:None,
            detection_yards=35,corridor_yards=18)
    assert all(c=='/targetenemy' for c in commands)
