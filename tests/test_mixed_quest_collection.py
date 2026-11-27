"""Regression for Graverobbers: completed kill stages must not block item farming."""
from dataclasses import replace
import pytest

from wotlkbuddy.horde.active_objectives import next_active_collect_quest
from wotlkbuddy.horde.combat import next_collect_objective
from wotlkbuddy.horde.profile import HordeProfile, OrderStep, QuestSpec, ObjectiveSpec, Source
from wotlkbuddy.live.codec import QuestRecord, QuestObjective
from wotlkbuddy.live.control import LiveControlError
from test_horde_combat_v019 import snap as base_snap


def graverobbers():
    specs=(ObjectiveSpec('KillMob',1941,8,(),()),
           ObjectiveSpec('KillMob',1675,5,(),()),
           ObjectiveSpec('CollectItem',2834,8,
                         tuple(Source('mob',entry,name) for entry,name in (
                             (1674,'Rot Hide Gnoll'),(1675,'Rot Hide Mongrel'),
                             (1941,'Rot Hide Graverobber'))),()))
    steps=tuple(OrderStep('objective',quest_id=358,name='Graverobbers',
                          entry=o.entry,objective_type=o.kind,count=o.count)
                for o in specs)
    profile=HordeProfile('Tirisfal','XML',(QuestSpec(358,'Graverobbers',specs),),
                         (),steps,.2,2,())
    quest=QuestRecord(358,8,False,'Graverobbers',(
        QuestObjective(1,True,'monster','Rot Hide Graverobber slain: 8/8'),
        QuestObjective(2,True,'monster','Rot Hide Mongrel slain: 5/5'),
        QuestObjective(3,False,'item','Embalming Ichor: 4/8')))
    return profile,replace(base_snap(),quests=(quest,),tracked_item_id=2834,
                           tracked_item_name='Embalming Ichor',tracked_item_count=4)


def test_graverobbers_collection_after_verified_kills():
    profile,state=graverobbers()
    spec,row=next_collect_objective(profile,state,358)
    assert spec.entry==2834 and row.index==3
    active=next_active_collect_quest(profile,state,map_id=0)
    assert active.quest_id==358 and active.sources==(1674,1675,1941)
    assert active.journal_description=='Embalming Ichor: 4/8'


def test_mixed_quest_cannot_skip_unfinished_kills():
    profile,state=graverobbers()
    q=state.quests[0]
    q=replace(q,objectives=(replace(q.objectives[0],done=False,
                  description='Rot Hide Graverobber slain: 7/8'),*q.objectives[1:]))
    with pytest.raises(LiveControlError,match='Mixed non-collection'):
        next_collect_objective(profile,replace(state,quests=(q,)),358)


def test_equal_kill_count_is_not_a_collection_item():
    profile,state=graverobbers()
    state=replace(state,tracked_item_id=None,tracked_item_name=None)
    spec,row=next_collect_objective(profile,state,358)
    assert row.index==3 and spec.entry==2834


def test_missing_vendor_item_still_rejected():
    profile,state=graverobbers()
    q=state.quests[0]
    q=replace(q,objectives=(*q.objectives,QuestObjective(4,False,'item','Coarse Thread: 0/1')))
    with pytest.raises(LiveControlError,match='objective count mismatch'):
        next_collect_objective(profile,replace(state,quests=(q,)),358)
