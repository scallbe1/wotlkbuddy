from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde import local_offers
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde.npc_visit import verified_npc_route


def test_partial_npc_path_is_recoverable():
    with pytest.raises(EncounterRouteUnavailable,match='partial path'):
        verified_npc_route([Vec3(0,0,0),Vec3(5,0,0)],
                           Vec3(0,0,0),Vec3(50,0,0))


def test_local_sweep_collects_with_active_quest_and_skips_failed_giver(monkeypatch):
    state=NS(player=NS(guid='player',level=9,dead=False,in_combat=False),
             quests=[NS(quest_id=371)],truncated=False,merchant_open=False,dialog=None)
    where=NS(position=Vec3(0,0,0),map_id=0)
    givers=[NS(name=n,entry=i,distance=d,position=Vec3(d,0,0),source='profile')
            for n,i,d in [('A',1,10),('B',2,20),('C',3,30)]]
    monkeypatch.setattr(local_offers,'nearby_profile_quest_givers',lambda **kw:givers)
    visits=[]
    def visit(**kw):
        visits.append(kw['npc_id'])
        if kw['npc_id']==1:
            raise EncounterRouteUnavailable('partial path')
    monkeypatch.setattr(local_offers,'visit_npc',visit)
    monkeypatch.setattr(local_offers,'accept_npc_offers',lambda **kw:(100+kw['npc_id'],))
    memory={}
    args=dict(profile=None,location=lambda:(state,where),cap=lambda:state,
              driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True),
              nav=None,db=None,pid=1,hud=Mock(),set_native_status=Mock(),log=Mock(),
              memory=memory,now=lambda:100.)
    assert local_offers.collect_profile_offers(**args)=={'B':(102,), 'C':(103,)}
    assert visits==[1,2,3]
    assert local_offers.collect_profile_offers(**args)=={}
    assert visits==[1,2,3]
    state.player.level=10
    assert local_offers.collect_profile_offers(**args)=={'B':(102,), 'C':(103,)}


def test_journal_reserve_prevents_more_local_pickups(monkeypatch):
    state=NS(quests=list(range(23)))
    discovery=Mock()
    monkeypatch.setattr(local_offers,'nearby_profile_quest_givers',discovery)
    assert local_offers.collect_profile_offers(profile=None,location=lambda:(state,None),
        cap=None,driver=None,nav=None,db=None,pid=1,hud=Mock(),set_native_status=Mock(),
        log=Mock(),memory={})=={}
    discovery.assert_not_called()


def test_empty_offer_has_longer_cooldown():
    from wotlkbuddy.horde.missed_quests import offer_retry_due
    player=NS(guid='p',level=11)
    memory={('p',11,10666):100.,('no_offer','p',11,10666):100.}
    assert not offer_retry_due(memory,player,10666,400.)
    assert offer_retry_due(memory,player,10666,1900.)
    player.level=12
    assert offer_retry_due(memory,player,10666,400.)
