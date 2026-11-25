"""Regression: a known defensive corpse may only be selectable at loot range."""
from dataclasses import replace
from types import SimpleNamespace as NS
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde import encounter_loot
from test_horde_combat_v019 import snap

GUID='0xF13000078F0040DD'
OTHER='0xF13000078F0040DF'

def simulate(monkeypatch, *, selectable=True):
    position=[Vec3(0,0,0)]
    target=['']
    sequence=[0]
    opened=[False]
    commands=[]
    routes=[]
    class Driver:
        keyboard=object()
        defeated_corpses={GUID:(1935,'Tirisfal Farmhand')}
        finished_corpses=set()
        def wow_is_foreground(self): return True
        def emergency_stop_requested(self): return False
        def sync_kills(self,s): pass
        def remember_defeated(self,s): pass
        def chat(self,c):
            commands.append(c)
            if c=='/targetlastenemy' and position[0].distance_to(Vec3(6.8,0,0))<=4.4:
                target[0]=GUID if selectable else OTHER
            if c.startswith('/run LootSlot'):
                opened[0]=False
        def target_objective_name(self,name):
            self.chat('/targetexact '+name)
            return True
        def interact(self):
            assert target[0]==GUID
            commands.append('interact')
            opened[0]=True
    d=Driver()
    def sample():
        sequence[0]+=1
        s=snap()
        s=replace(s,sequence=sequence[0],player=replace(s.player,target_guid=target[0]),
            combat_target=replace(s.combat_target,entry_id=1935 if target[0] else 0,
                dead=bool(target[0])),loot_open=opened[0],loot_count=1 if opened[0] else 0,
            loot_sequence=1 if opened[0] else 0)
        p=position[0]
        return s,NS(position=p,x=p.x,y=p.y,z=p.z,map_id=0)
    def travel(path,**kwargs):
        routes.append(path)
        position[0]=path[-1]
        return NS(stopped_reason='complete')
    monkeypatch.setattr(encounter_loot,'follow_world_path',travel)
    nav=NS(find_path=lambda _,a,b:(a,b),segment_clear=lambda *_:True)
    encounter_loot.loot_encounter(initial=sample()[0],sample=sample,driver=d,nav=nav,
        corpse_position=lambda s,g:Vec3(6.8,0,0),sleep=lambda _:None)
    return d,commands,routes

def test_defensive_corpse_approached_before_selection(monkeypatch):
    d,commands,routes=simulate(monkeypatch)
    assert len(routes)==1
    assert routes[0][-1].distance_to(Vec3(6.8,0,0))<=4.4
    assert commands==['/targetlastenemy','interact','/run LootSlot(1)']
    assert GUID in d.finished_corpses

def test_same_name_wrong_guid_never_looted(monkeypatch):
    d,commands,routes=simulate(monkeypatch,selectable=False)
    assert routes
    assert 'interact' not in commands
    assert GUID in d.defeated_corpses
