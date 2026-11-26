from types import SimpleNamespace as NS

from wotlkbuddy.combat.consumables import emergency_heal, ready_healing_potion
from wotlkbuddy.horde.merchant import safe_sale_candidates
from wotlkbuddy.live.codec import MerchantItem


def snapshot(hp=30, item=118, qty=2, combat=True):
    return NS(truncated=False, player=NS(guid='player',dead=False,
        in_combat=combat,health=hp,max_health=100),
        mage_supplies=NS(potion_bag=0,potion_slot=5,
            potion_item_id=item,potion_quantity=qty))


def test_emergency_healing_requires_low_health_combat_and_known_item():
    assert ready_healing_potion(snapshot())==(0,5,118,2)
    assert ready_healing_potion(snapshot(hp=80)) is None
    assert ready_healing_potion(snapshot(combat=False)) is None
    assert ready_healing_potion(snapshot(item=5350)) is None


def test_emergency_use_verifies_slot_and_limits_repeat():
    commands=[]
    driver=NS(chat=commands.append,emergency_stop_requested=lambda:False,
              wow_is_foreground=lambda:True)
    before=snapshot()
    frames=iter([snapshot(),snapshot(hp=70,qty=1)])
    healed=emergency_heal(before,driver=driver,
        fresh=lambda *_:next(frames),log=lambda _:None,now=lambda:100)
    assert healed.player.health==70
    assert commands==['/use 0 5']
    emergency_heal(before,driver=driver,
        fresh=lambda *_:None,log=lambda _:None,now=lambda:101)
    assert len(commands)==1


def test_slot_change_prevents_use():
    commands=[]
    driver=NS(chat=commands.append,emergency_stop_requested=lambda:False,
              wow_is_foreground=lambda:True)
    emergency_heal(snapshot(),driver=driver,fresh=lambda *_:snapshot(item=858),
                   log=lambda _:None,now=lambda:100)
    assert not commands


def test_vendor_protects_potions_and_hearthstone_even_if_reported():
    items=tuple(MerchantItem(0,i,item,1,1,1,0,False,'VENDOR_JUNK')
                for i,item in enumerate((118,858,2455,6948,2589,3434),1))
    state=NS(truncated=False,merchant_open=True,merchant_scan_complete=True,
             resources=NS(),inventory_sale_scan=False,merchant_items=items)
    assert [i.item_id for i in safe_sale_candidates(state,protected_item_ids=frozenset())]==[2589,3434]
    assert [i.item_id for i in safe_sale_candidates(state,protected_item_ids=frozenset({2589}))]==[3434]


def test_broad_white_vendor_stock_sells_priced_food_water_but_protects_quest_items():
    ids=(5350,5349,6948,118,2455,2589,3434,2875,999)
    items=tuple(MerchantItem(0,i,item,1,1,1,0,False,'VENDOR_JUNK')
                for i,item in enumerate(ids,1))
    state=NS(truncated=False,merchant_open=True,merchant_scan_complete=True,
             resources=NS(),inventory_sale_scan=False,merchant_items=items)
    assert [i.item_id for i in safe_sale_candidates(state,
        protected_item_ids=frozenset({2875}))]==[5350,5349,2589,3434,999]


def test_zero_value_conjured_supplies_not_sent_to_vendor():
    items=(MerchantItem(0,1,5350,10,1,0,0,False,'VENDOR_JUNK'),
           MerchantItem(0,2,5349,10,1,0,0,False,'VENDOR_JUNK'))
    state=NS(truncated=False,merchant_open=True,merchant_scan_complete=True,
             resources=NS(),inventory_sale_scan=False,merchant_items=items)
    assert not safe_sale_candidates(state,protected_item_ids=frozenset())
