"""Generic catch-up: no zone, race, class or quest-ID whitelist."""
from dataclasses import replace
from types import SimpleNamespace as NS
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile,OrderStep,load_horde_profile,native_profiles_root
from wotlkbuddy.horde.missed_quests import recovery_offers
from wotlkbuddy.horde.quest_metadata import QuestMetadata,read_quest_metadata


def fixture():
    state=NS(truncated=False,player=NS(guid='player',level=11,dead=False,in_combat=False),
             resources=NS(class_token='DRUID',race_token='Tauren'),
             quests=(NS(quest_id=900,complete=False),),
             quest_completions=((800,True),))
    current=NS(map_id=1,position=Vec3(0,0,0))
    steps=tuple(OrderStep('pickup',qid,f'Quest {qid}',entry,world_position=Vec3(distance,0,0),
                   npc_name=f'Giver {entry}',conditions=conditions)
                for qid,entry,distance,conditions in ((701,1,300,('Me.Class == WoWClass.Druid',)),
                    (702,2,50,()),(703,3,250,()),(704,4,80,()),
                    (705,5,700,()),(706,6,70,('Me.Race == WoWRace.Undead',))))
    profile=HordeProfile('Arbitrary starter','imported.xml',(),(),steps+(
        OrderStep('objective',900,'Active quest'),),.2,2,())
    points={step.entry:step.world_position for step in steps}
    class DB:
        def find(self,entry,map_id,limit):
            assert map_id==1
            return [NS(position=points[entry])] if entry in points else []
    return profile,state,current,DB()


def ids(offers):
    return {qid for offer in offers for qid in offer.quest_ids}


def meta(qid,level,prev=0,races=0,classes=0):
    return QuestMetadata(qid,1,level,0,races,classes,prev,0,2)


def test_arbitrary_profile_character_and_level_based_ranking():
    p,s,c,db=fixture()
    rules={701:meta(701,3,800,32,1024),702:meta(702,7),
           703:meta(703,12),704:meta(704,5,999),705:meta(705,1),706:meta(706,1)}
    found=recovery_offers(p,s,c,db,{},now=1000,metadata=rules)
    assert [o.quest_ids for o in found]==[frozenset({701}),frozenset({702})]
    assert found[0].giver.distance==300


def test_without_metadata_uses_earlier_profile_opportunities_not_whitelist():
    p,s,c,db=fixture()
    found=recovery_offers(p,s,c,db,{},now=1000,metadata={})
    assert ids(found)=={701,702,703,704}
    s.quest_completions=((701,True),)
    assert not {701,702}&ids(recovery_offers(p,s,c,db,{('player',11,2):999},now=1000,metadata={}))
    s.quests=(NS(quest_id=900,complete=True),)
    assert not recovery_offers(p,s,c,db,{},now=1000,metadata={})


def test_calvin_followup_is_not_excluded_by_quest_id():
    p=load_horde_profile(native_profiles_root()/'Horde/Starter/Tirisfal-Glades-1-12.xml')
    step=next(s for s in p.steps if s.kind=='pickup' and s.quest_id==590)
    s=NS(truncated=False,player=NS(guid='p',level=12,dead=False,in_combat=False),
         resources=NS(class_token='MAGE',race_token='Scourge'),
         quests=(),quest_completions=((8,True),))
    c=NS(map_id=0,position=step.world_position)
    db=NS(find=lambda entry,map_id,limit:[NS(position=step.world_position)] if entry==step.entry else [])
    assert 590 in ids(recovery_offers(p,s,c,db,{},now=1000,metadata={}))
    s.quest_completions=((8,True),(590,True))
    assert 590 not in ids(recovery_offers(p,s,c,db,{},now=1000,metadata={}))


def test_server_metadata_reads_actual_ids_and_checks_masks_and_dependencies(tmp_path):
    path=tmp_path/'QuestMetadata.local.tsv'
    path.write_text('701\t1\t5\t0\t32\t1024\t800\t0\t2\n',encoding='utf8')
    rule=read_quest_metadata(path)[701]
    _,s,_,_=fixture()
    assert rule.permits(s,set(),{800})
    assert not rule.permits(s,set(),set())
    s.resources.class_token='MAGE'
    assert not rule.permits(s,set(),{800})
    path.write_text('invalid\n',encoding='utf8')
    import pytest
    with pytest.raises(ValueError):
        read_quest_metadata(path)
