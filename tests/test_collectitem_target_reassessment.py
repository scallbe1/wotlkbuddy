from types import SimpleNamespace as NS

import pytest

from wotlkbuddy.horde.hunt_attack import TargetReassessment, confirm_lost_target
from wotlkbuddy.horde.hunt_engagement import execute_engagement, BlockedEngagements


def frame(guid, *, dead=False, combat=False):
    return NS(player=NS(target_guid=guid,in_combat=combat),
              combat_target=NS(entry_id=1520,dead=dead))


def test_switched_selection_can_reselect_verified_original_corpse():
    calls=[]
    original=frame('original',dead=True)
    result=confirm_lost_target(frame('other'),collect_items=True,
        target_guid='original',expected=1520,driver=NS(chat=calls.append),
        fresh=lambda _:original,credit_advanced=lambda _:False,
        record_kill=calls.append,log=lambda _:None)
    assert result is original
    assert calls==['/targetlasttarget',1520]


@pytest.mark.parametrize('observed',[frame('other',dead=True),frame('original')])
def test_unverified_corpse_never_awards_kill_credit(observed):
    calls=[]
    with pytest.raises(TargetReassessment,match='kill not verified'):
        confirm_lost_target(frame('other'),collect_items=True,
            target_guid='original',expected=1520,driver=NS(chat=calls.append),
            fresh=lambda _:observed,credit_advanced=lambda _:False,
            record_kill=calls.append,log=lambda _:None)
    assert calls==['/targetlasttarget']


@pytest.mark.parametrize('combat',[False,True])
def test_interrupted_selection_resumes_hunt_without_unverified_loot(combat):
    start=frame('original')
    switched=frame('other',combat=combat)
    events=[]
    def attack(*args):
        raise TargetReassessment('selection changed')
    def defend(s):
        events.append('defend')
        return frame('other')
    def forbidden(*args,**kwargs):
        raise AssertionError('No unverified loot or objective accounting')
    blocked=BlockedEngagements()
    ignored=set()
    execute_engagement(start,1520,None,blocked=blocked,unhittable_guids=ignored,
        unhittable_positions=[],objectives=NS(after_loot=forbidden),collect_items=True,
        spec=None,observed=NS(index=1,description='Notched Rib: 0/5'),
        qlog=lambda _:NS(objectives=()),attack=attack,capture=lambda:switched,
        guard=lambda _:None,driver=NS(),fresh=forbidden,clear_remaining_aggro=defend,
        loot=forbidden,log=events.append)
    assert blocked.count==0 and not ignored
    assert ('defend' in events)==combat
    assert 'refreshing objective' in events[-1]
