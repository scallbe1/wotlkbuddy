"""Server-verified vendor objective and purchase evidence regression."""
from types import SimpleNamespace as NS
import pytest

from wotlkbuddy.horde.profile import load_horde_profile, native_profiles_root
from wotlkbuddy.horde.quest_vendor import buy_thread_at_vendor, thread_vendor
from wotlkbuddy.live.control import LiveControlError


def test_native_thread_is_vendor_sourced_and_first_in_quest_order():
    p=load_horde_profile(native_profiles_root()/'Horde/Starter/Tirisfal-Glades-1-12.xml')
    objective=NS(quest_id=375,item_id=2320,required_count=1)
    v=thread_vendor(p,objective)
    assert v.entry==2118 and v.name=='Abigail Shiel'
    q=next(q for q in p.quests if q.quest_id==375)
    assert {o.entry:o.count for o in q.objectives}=={2320:1,2876:5}
    assert [s.entry for s in p.steps if s.kind=='objective' and s.quest_id==375]==[2320,2876]
    assert thread_vendor(p,NS(quest_id=375,item_id=2876,required_count=5)) is None


class Game:
    def __init__(self,confirm=True):
        self.seq=0
        self.time=0.
        self.open=False
        self.tracked=False
        self.count=0
        self.cash=20
        self.done=False
        self.commands=[]
        self.confirm=confirm

    def capture(self):
        self.seq+=1
        return NS(sequence=self.seq,truncated=False,
                  player=NS(guid='player',target_guid='0xF130000846000001',
                            target_name='Abigail Shiel',dead=False,in_combat=False),
                  resources=NS(class_token='MAGE',money_copper=self.cash),
                  quests=(NS(quest_id=375,objectives=(NS(kind='item',done=self.done,
                         description=f'Coarse Thread: {int(self.done)}/1'),)),),
                  tracked_item_id=2320 if self.tracked else None,
                  tracked_item_name='Coarse Thread' if self.tracked else None,
                  tracked_item_count=self.count if self.tracked else None,
                  merchant_open=self.open,dialog=None)
    def sleep(self,seconds): self.time+=seconds
    def now(self): return self.time
    def emergency_stop_requested(self): return False
    def wow_is_foreground(self): return True
    def interact(self): self.open=True
    def chat(self,cmd):
        self.commands.append(cmd)
        if cmd=='/wbttrack 2320':
            self.tracked=True
        elif cmd=='/wbtbuythread':
            assert self.open and self.count==0
            self.count=1
            self.cash-=10
            self.done=self.confirm
        elif cmd=='/click MerchantFrameCloseButton':
            self.open=False


def purchase(g):
    return buy_thread_at_vendor(capture=g.capture,driver=g,log=lambda _:None,
                                sleep=g.sleep,now=g.now)


def test_purchase_requires_inventory_cash_and_journal_confirmation():
    g=Game()
    assert purchase(g)==dict(quest_id=375,item_id=2320,npc_id=2118,copper=10)
    assert not g.open and g.commands.count('/wbtbuythread')==1


def test_ambiguous_purchase_never_repeats_and_closes_window():
    g=Game(confirm=False)
    with pytest.raises(LiveControlError,match='purchase not confirmed'):
        purchase(g)
    assert not g.open and g.commands.count('/wbtbuythread')==1


def test_insufficient_money_never_issues_purchase():
    g=Game()
    g.cash=9
    with pytest.raises(LiveControlError,match='funds not verified'):
        purchase(g)
    assert not g.commands
