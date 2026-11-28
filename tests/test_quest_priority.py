from dataclasses import replace
from types import SimpleNamespace as NS
from argparse import Namespace
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.quest_priority import quest_ranker
from wotlkbuddy.horde.session_objectives import select_objective
from test_active_objectives_v025 import active,profile
from test_postquest_v021 import snap


def test_lower_level_later_kill_quest_beats_earlier_one():
    state=snap(quests=(replace(active(3901,'Rattling the Rattlecages'),level=10),
                       replace(active(5001,'Later kill quest'),level=4)))
    mode,chosen,_,_=select_objective(Namespace(only='all'),profile(),state,
                                      NS(map_id=0,position=Vec3(0,0,0)))
    assert mode=='kill' and chosen.quest_id==5001


def test_lower_level_collect_quest_beats_higher_level_kill():
    state=snap(quests=(replace(active(376,'The Damned','item'),level=4),
                       replace(active(3901,'Rattling the Rattlecages'),level=10)))
    mode,chosen,_,_=select_objective(Namespace(only='all'),profile(),state,
                                      NS(map_id=0,position=Vec3(0,0,0)))
    assert mode=='collect' and chosen.quest_id==376


def test_progress_then_hotspot_then_profile_order_break_level_ties():
    a=active(3901,'Rattling the Rattlecages')
    b=active(5001,'Later kill quest')
    b=replace(b,objectives=(replace(b.objectives[0],description='7/8'),))
    base=profile()
    state=snap(quests=(a,b))
    candidates=[NS(quest_id=3901,profile_step=3),NS(quest_id=5001,profile_step=4)]
    where=NS(position=Vec3(0,0,0))
    assert min(candidates,key=quest_ranker(base,state,where)).quest_id==5001
    b=replace(b,objectives=a.objectives)
    state=snap(quests=(a,b))
    specs=list(base.quests)
    for index,distance in ((1,100),(2,10)):
        specs[index]=replace(specs[index],objectives=(replace(specs[index].objectives[0],
            hotspots=(Vec3(distance,0,0),)),))
    assert min(candidates,key=quest_ranker(replace(base,quests=tuple(specs)),state,where)).quest_id==5001
    assert min(candidates,key=quest_ranker(base,state,where)).quest_id==3901


def test_route_failure_temporarily_demotes_lower_level_quest():
    import pytest
    from wotlkbuddy.horde.quest_priority import defer_blocked_quest,rank_with_deferrals
    from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
    a=replace(active(3901,'Rattling the Rattlecages'),level=4)
    b=replace(active(5001,'Later kill quest'),level=10)
    state=snap(quests=(a,b))
    memory={}
    with pytest.raises(EncounterRouteUnavailable):
        with defer_blocked_quest(state,3901,memory,now=lambda:100.):
            raise EncounterRouteUnavailable('blocked terrain')
    candidates=[NS(quest_id=3901,profile_step=3),NS(quest_id=5001,profile_step=4)]
    rank=quest_ranker(profile(),state,NS(position=Vec3(0,0,0)))
    assert min(candidates,key=rank_with_deferrals(rank,state,memory,now=lambda:101.)).quest_id==5001
    assert min(candidates,key=rank_with_deferrals(rank,state,memory,now=lambda:221.)).quest_id==3901


def test_deferral_is_cleared_by_progress_and_does_not_mask_telemetry_failure():
    import pytest
    from wotlkbuddy.horde.quest_priority import active_deferrals,defer_blocked_quest
    from wotlkbuddy.live.control import LiveControlError
    a=active(3901,'Rattling the Rattlecages')
    state=snap(quests=(a,))
    key=(state.player.guid,3901)
    memory={key:(220.,state.player.level,0.)}
    changed=replace(state,quests=(replace(a,objectives=(replace(a.objectives[0],description='1/8'),)),))
    assert active_deferrals(changed,memory,now=lambda:101.)=={}
    with pytest.raises(LiveControlError,match='focus lost'):
        with defer_blocked_quest(state,3901,memory,now=lambda:100.):
            raise LiveControlError('focus lost')
    assert not memory
