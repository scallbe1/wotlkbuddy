from types import SimpleNamespace as NS
import pytest
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde import npc_catalog, merchant
from wotlkbuddy.horde.npc_visit import NpcTargetUnavailable
from wotlkbuddy.horde.worlddata import Spawn


def test_merchants_combine_catalogs_and_rank_by_distance(monkeypatch):
    def spec(entry,name,x):
        return NS(entry=entry,name=name,kind='vendor',world_position=Vec3(x,0,0))
    profile=NS(vendors=(spec(3547,'Hamlin Atkins',40),),source_path='profile.xml')
    monkeypatch.setattr(npc_catalog,'load_horde_vendor_catalog',
        lambda *args,**kwargs:(spec(2135,'Abe Winters',20),))
    monkeypatch.setattr(npc_catalog,'_curated_vendor_identities',
        lambda:((2118,'Abigail Shiel','general'),))
    spawns=NS(find=lambda entry,m,limit:[Spawn(entry,m,{3547:40,2135:20,2118:10}[entry],0,0)])
    options=npc_catalog.nearby_service_npcs(profile=profile,spawns=spawns,
        origin=Vec3(0,0,0),map_id=0,role='merchant',profiles_root='profiles')
    assert [v.entry for v in options]==[2118,2135,3547]


@pytest.mark.parametrize('failure',[NpcTargetUnavailable('NPC not verified after targeting'),EncounterRouteUnavailable('NPC destination unavailable: partial path')])
def test_unselectable_merchant_falls_back_to_another(monkeypatch,failure):
    state=NS(truncated=False,player=NS(guid='player',dead=False,in_combat=False),
             resources=NS(free_bag_slots=1),merchant_open=False,dialog=None)
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    world=NS(position=Vec3(0,0,0),map_id=0)
    options=tuple(npc_catalog.NpcDestination(name,entry,Vec3(x,0,0),x,'catalog','merchant')
                  for name,entry,x in [('Hamlin Atkins',3547,10),('Abigail Shiel',2118,20)])
    monkeypatch.setattr(merchant,'verified_live_position',lambda s,pid:(pid,world))
    monkeypatch.setattr(merchant,'nearby_service_npcs',lambda **kw:options)
    visits=[]
    def visit(**kw):
        visits.append(kw['npc_id'])
        if kw['npc_id']==3547:
            raise failure
    monkeypatch.setattr(merchant,'visit_npc',visit)
    monkeypatch.setattr(merchant,'sell_at_verified_merchant',lambda **kw:kw['npc_id'])
    assert merchant.free_bag_space(profile=None,profiles_root='profiles',pid=1,
        capture=lambda:state,driver=driver,nav=None,spawns=None)==2118
    assert visits==[3547,2118]
