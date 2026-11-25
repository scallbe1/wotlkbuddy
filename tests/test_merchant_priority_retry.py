from dataclasses import replace
import pytest
from types import SimpleNamespace as NS
from test_vendor_recovery_v029 import MerchantGame,profile,NPC,ENTRY
from wotlkbuddy.horde.merchant import (sell_at_verified_merchant,MerchantUnavailable,
    should_vendor_before_training)

def test_small_sale_not_needed_when_funded_and_bags_available():
    g=MerchantGame()
    s=g.capture()
    s=replace(s,resources=replace(s.resources,money_copper=400,free_bag_slots=5))
    goal=NS(pending=(),next_cost=None)
    assert not should_vendor_before_training(s,goal,4)
    assert should_vendor_before_training(s,NS(pending=(('Frostbolt','Rank 1',500),),next_cost=500),4)
    assert should_vendor_before_training(replace(s,resources=replace(s.resources,free_bag_slots=1)),goal,4)
    assert not should_vendor_before_training(s,goal,0)

def test_merchant_retries_then_sells():
    g=MerchantGame(initial='none')
    attempts=[]
    def interact():
        attempts.append(1)
        if len(attempts)==2:
            g.mode='gossip'
    g.driver.on_interact=interact
    report=sell_at_verified_merchant(npc_name=NPC,npc_id=ENTRY,profile=profile(),
        capture=g.capture,driver=g.driver,now=g.clock.now,sleep=g.clock.sleep)
    assert len(attempts)==2
    assert report.copper_received==17

def test_unresponsive_merchant_is_distinct_and_no_sale_sent():
    g=MerchantGame(initial='none')
    attempts=[]
    g.driver.on_interact=lambda:attempts.append(1)
    with pytest.raises(MerchantUnavailable,match='three attempts'):
        sell_at_verified_merchant(npc_name=NPC,npc_id=ENTRY,profile=profile(),
            capture=g.capture,driver=g.driver,now=g.clock.now,sleep=g.clock.sleep)
    assert len(attempts)==3
    assert not any('UseContainerItem' in cmd for cmd in g.driver.commands)
