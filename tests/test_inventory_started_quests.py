from types import SimpleNamespace as NS
from wotlkbuddy.live.codec import parse_item_quest_starter, TelemetryError
from wotlkbuddy.horde.item_quests import accept_item_quests
import pytest

def test_item_starter_record():
    rows=parse_item_quest_starter(['IQ','0','2','123','456','0','0'],{})
    assert rows[(0,2)]==(0,2,123,456,0,0)
    with pytest.raises(TelemetryError):
        parse_item_quest_starter(['IQ','0','2','123','456','0','0'],rows)

@pytest.mark.parametrize('merchant,detail_id,expected',[(False,456,(456,)),(True,456,()),(False,999,())])
def test_item_quest_acceptance(merchant,detail_id,expected):
    calls=[]
    sequence=[0]
    def capture():
        sequence[0]+=1
        used=any('UseContainerItem' in cmd for cmd in calls)
        accepted='/run AcceptQuest()' in calls
        return NS(sequence=sequence[0],player=NS(guid='owner',dead=False,in_combat=False),
                  truncated=False,merchant_open=merchant,trainer_open=False,loot_open=False,
                  quests=(NS(quest_id=456),) if accepted else (),quest_completions=(),
                  item_quest_starters=() if accepted else ((0,2,123,456,0,0),),
                  dialog=NS(kind='detail' if used and not accepted else 'none',
                            quest_id=detail_id,title='Quest from item'))
    driver=NS(wow_is_foreground=lambda:True,emergency_stop_requested=lambda:False,chat=calls.append)
    assert accept_item_quests(capture=capture,driver=driver,sleep=lambda _:None)==expected
    assert ('/run AcceptQuest()' in calls)==bool(expected)
    if merchant:
        assert not calls
