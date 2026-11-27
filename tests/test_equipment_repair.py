"""Verify repair readiness, wallet/durability evidence and deferred attempts."""
from types import SimpleNamespace as NS
import pytest
from wotlkbuddy.horde.repair import repair_at_merchant, maintain_equipment, repair_threshold
from wotlkbuddy.live.control import LiveControlError


class Merchant:
    def __init__(self, repair=True):
        self.repair=repair
        self.sequence=0
        self.time=0.
        self.open=False
        self.durability=30
        self.cash=250
        self.commands=[]
    def capture(self):
        self.sequence+=1
        return NS(sequence=self.sequence,truncated=False,
                  player=NS(guid='player',target_guid='0xF130000DC2000001',
                            target_name='Constance Brisboise',dead=False,in_combat=False),
                  resources=NS(class_token='MAGE',durability_percent=self.durability,
                               money_copper=self.cash),merchant_open=self.open,dialog=None)
    def emergency_stop_requested(self): return False
    def wow_is_foreground(self): return True
    def interact(self): self.open=True
    def now(self): return self.time
    def sleep(self,seconds): self.time+=seconds
    def chat(self,cmd):
        self.commands.append(cmd)
        if cmd.startswith('/wbtrepair ') and self.repair:
            self.durability=100
            self.cash-=25
        elif cmd=='/click MerchantFrameCloseButton':
            self.open=False


def transaction(m):
    return repair_at_merchant(npc_name='Constance Brisboise',npc_id=3522,
        capture=m.capture,driver=m,log=lambda _:None,sleep=m.sleep,now=m.now)


def test_repair_is_confirmed_and_merchant_closed():
    m=Merchant()
    assert transaction(m)==dict(status='repaired',before=30,after=100,copper=25,npc_id=3522)
    assert m.commands==['/wbtrepair 3522 250','/click MerchantFrameCloseButton']
    assert not m.open


def test_unconfirmed_repair_does_not_repeat():
    m=Merchant(repair=False)
    assert transaction(m)['status']=='deferred'
    assert m.commands.count('/wbtrepair 3522 250')==1
    assert not m.open


def test_healthy_and_unaffordable_equipment_policy():
    m=Merchant()
    p=NS(min_durability=.2)
    assert repair_threshold(p)==50
    assert repair_threshold(NS(min_durability=.6))==60
    kwargs=dict(profile=p,profiles_root='',location=lambda:(m.capture(),None),
                cap=m.capture,driver=m,pid=1,nav=None,db=None,hud=None,
                set_native_status=lambda _:None,log=lambda _:None)
    m.durability=80
    assert maintain_equipment(**kwargs) is None
    m.durability=30
    m.cash=0
    assert maintain_equipment(**kwargs)['status']=='deferred'
    m.durability=20
    with pytest.raises(LiveControlError,match='Critical equipment durability'):
        maintain_equipment(**kwargs)
    assert not m.commands
