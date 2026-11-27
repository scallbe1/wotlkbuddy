"""Catch-up is bounded, prerequisite-aware and restricted to native profile."""
from dataclasses import replace
from types import SimpleNamespace as NS
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import load_horde_profile, native_profiles_root
from wotlkbuddy.horde.missed_quests import recovery_offers


def fixture():
    profile=load_horde_profile(native_profiles_root()/'Horde/Starter/Tirisfal-Glades-1-12.xml')
    state=NS(truncated=False,player=NS(guid='player',level=11,dead=False,in_combat=False),
             resources=NS(class_token='MAGE',race_token='Scourge'),
             quests=(NS(quest_id=371,complete=False),),
             quest_completions=((358,True),))
    current=NS(map_id=0,position=Vec3(2250,280,35))
    points={s.entry:s.world_position for s in profile.steps
            if s.kind=='pickup' and s.world_position}
    class DB:
        def find(self,entry,map_id,limit):
            return [NS(position=points[entry])] if entry in points else []
    return profile,state,current,DB()


def ids(offers):
    return {qid for offer in offers for qid in offer.quest_ids}


def test_earlier_quests_recovered_beyond_local_radius():
    p,s,c,db=fixture()
    offers=recovery_offers(p,s,c,db,{},now=1000)
    assert {5481,354,362,359,356}.issubset(ids(offers))
    assert 5482 not in ids(offers)  # Gordo prerequisite not rewarded
    assert 365 not in ids(offers)  # beyond 600y, don't authorize long-distance travel
    assert any(o.giver.distance>160 for o in offers)
    assert all(o.giver.distance<=600 for o in offers)


def test_completion_and_retry_memory_prevent_unnecessary_visits():
    p,s,c,db=fixture()
    s.quest_completions=((358,True),(5481,True),(354,True))
    offers=recovery_offers(p,s,c,db,{('player',11,1500):999},now=1000)
    assert 5482 in ids(offers)
    assert not {5481,354,362}&ids(offers)
    s.quests=(NS(quest_id=371,complete=True),)
    assert not recovery_offers(p,s,c,db,{},now=1000)
    s.quests=()
    assert not recovery_offers(replace(p,source_path='imported.xml'),s,c,db,{},now=1000)


def test_do_not_start_later_or_above_level_quests():
    p,s,c,db=fixture()
    s.player.level=8
    assert not {354,362,359,356}&ids(recovery_offers(p,s,c,db,{},now=1000))
    s.player.level=11
    s.quests=(NS(quest_id=358,complete=False),)
    s.quest_completions=()
    assert not {359,356}&ids(recovery_offers(p,s,c,db,{},now=1000))
