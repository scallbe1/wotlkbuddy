from dataclasses import replace
from test_postquest_v021 import snap,Clock,Driver
from wotlkbuddy.live.codec import BagArmor,EquippedArmor,MerchantItem,parse_payload
from wotlkbuddy.horde.gear import best_cloth_upgrade,equip_mage_upgrades
from wotlkbuddy.horde.merchant import estimated_vendor_copper,safe_sale_candidates
from wotlkbuddy.horde.training_goals import TrainingGoal
from types import SimpleNamespace

def test_staff_upgrade_and_displaced_weapon_becomes_funding():
    staff=BagArmor(0,1,3277,16,9,1,10,True,'Executor Staff')
    old=BagArmor(0,1,35,16,2,1,0,True,'Bent Staff')
    old_sale=MerchantItem(0,1,35,1,1,20,0,False,'INVTYPE_2HWEAPON')
    new_sale=replace(old_sale,item_id=3277,vendor_price=30)
    before=snap(gear_scan_complete=True,weapon_swap_safe=True,inventory_sale_scan=True,
        merchant_scan_complete=True,bag_armor=(staff,),
        equipped_armor=(EquippedArmor(16,35,2,0),),merchant_items=(new_sale,))
    profile=SimpleNamespace(quests=())
    assert estimated_vendor_copper(before,profile)==0
    assert best_cloth_upgrade(before)==staff
    assert best_cloth_upgrade(replace(before,weapon_swap_safe=False)) is None
    after=replace(before,bag_armor=(old,),equipped_armor=(EquippedArmor(16,3277,9,10),),merchant_items=(old_sale,))
    assert estimated_vendor_copper(after,profile)==20
    g=TrainingGoal(6,81,8,(('Fireball','Rank 2',95),),'Isabella',2124,0,(1,2,3),0)
    assert g.due(6,81,8,vendor_copper=20)
    assert not g.due(6,81,8,vendor_copper=0)
    assert not g.due(6,81,8,vendor_copper=10)
    assert best_cloth_upgrade(after) is None
    clock=Clock()
    d=Driver()
    state={'equipped':False,'seq':0}
    def capture():
        state['seq']+=1
        return replace(after if state['equipped'] else before,sequence=state['seq'])
    def action(cmd):
        if cmd=='/run EquipItemByName(3277,16)': state['equipped']=True
    d.on_chat=action
    assert equip_mage_upgrades(capture=capture,driver=d,now=clock.now,sleep=clock.sleep)==(3277,)

def test_closed_inventory_estimate_not_merchant_permission():
    raw=(b'P\tAarr\t0x000000000000025F\t6\t117\t117\t0\t0\t0.5\t0.6\t2\t12\tDeathknell\t\t\n'
         b'S\t360\t360\t8\t16\t100\t0\t81\tMAGE\tScourge\n'
         b'U\t0\t1\t3277\t16\t9\t1\t10\t1\tExecutor Staff\nW\t16\t35\t2\t0\nZ\t1\nWG\t1\n'
         b'M\t1\t1\t0\nD\t0\t1\t3277\t1\t1\t30\t0\t0\tINVTYPE_2HWEAPON\n')
    s=parse_payload(raw,1)
    assert s.weapon_swap_safe and s.inventory_sale_scan
    assert s.merchant_scan_complete and not s.merchant_open
    assert estimated_vendor_copper(s,SimpleNamespace(quests=()))==0
