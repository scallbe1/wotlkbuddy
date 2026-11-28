from dataclasses import replace
from argparse import Namespace
from types import SimpleNamespace as NS
from pathlib import Path
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import load_horde_profile
from wotlkbuddy.horde.quest_batching import choose_ready_turnin, local_batch_plan
from wotlkbuddy.live.codec import QuestRecord,QuestObjective
from test_postquest_v021 import snap


def fixture():
    root=Path(__file__).resolve().parents[1]
    profile=load_horde_profile(root/'src/wotlkbuddy/profiles/Horde/Starter/Tirisfal-Glades-1-12.xml')
    base=snap()
    family=QuestRecord(354,11,False,'Deaths in the Family',(
        QuestObjective(1,True,'item','1/1'),QuestObjective(2,True,'item','1/1'),
        QuestObjective(3,False,'item','0/1')))
    letter=QuestRecord(361,8,True,'A Letter Undelivered',())
    state=replace(base,player=replace(base.player,level=11),quests=(family,letter))
    current=NS(map_id=0,position=Vec3(2848.48,1018.58,117.90))
    return profile,state,current,Namespace(only='objectives',_continuous_training_check=True)


def test_agamand_mills_finishes_thurman_before_remote_letter():
    profile,state,current,args=fixture()
    logs=[]
    assert choose_ready_turnin(args,profile,state,current,log=logs.append) is None
    mode,objective,*_=local_batch_plan(args,profile,state,current)
    assert mode=='collect' and objective.quest_id==354 and objective.item_id==2830
    assert objective.sources==(1656,)
    assert 'Geographic quest batch' in logs[0]


def test_no_batch_when_local_quest_deferred_or_journal_near_capacity():
    from time import monotonic
    from wotlkbuddy.horde.quest_priority import quest_progress
    profile,state,current,args=fixture()
    args._quest_deferrals={(state.player.guid,354):(monotonic()+120,11,quest_progress(state.quests[0]))}
    assert choose_ready_turnin(args,profile,state,current,log=lambda _:None).quest_id==361
    args._quest_deferrals={}
    state=replace(state,resources=replace(state.resources,free_bag_slots=2))
    assert choose_ready_turnin(args,profile,state,current,log=lambda _:None).quest_id==361


def test_explicit_turnin_mode_is_not_deferred_and_nearby_receiver_wins():
    profile,state,current,args=fixture()
    args.only='turnin'
    assert choose_ready_turnin(args,profile,state,current,log=lambda _:None).quest_id==361
    args.only='objectives'
    current.position=Vec3(2252.83,250.671,34.3437)
    assert choose_ready_turnin(args,profile,state,current,log=lambda _:None).quest_id==361


def test_completed_local_batch_report_continues_without_offer_phase():
    from test_session_controller import session
    s=session()
    assert not s.handle_objectives({'active_objective':{'status':'complete'},
        'deferred_turnin':{'quest_id':354,'next_local_quest_id':362}},1)
    assert s.reason=='cycle_limit' and s.steps[-1]['action']=='deferred_turnin'
