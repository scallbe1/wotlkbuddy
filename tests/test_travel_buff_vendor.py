from types import SimpleNamespace as NS
from wotlkbuddy.horde.merchant import protected_profile_items,safe_sale_candidates
from wotlkbuddy.live.codec import MerchantItem
from wotlkbuddy.horde.recovery_rest import restore_self_buff
from unittest.mock import Mock

def test_completed_quest_item_no_longer_protected_unless_needed_elsewhere():
    obj=NS(kind='CollectItem',entry=123)
    profile=NS(quests=(NS(quest_id=1,objectives=(obj,)),))
    state=NS(quests=(),quest_completions=((1,True),))
    assert protected_profile_items(state,profile)==frozenset()
    state.quest_completions=()
    assert protected_profile_items(state,profile)==frozenset({123})
    state.quest_completions=((1,True),)
    state.quests=(NS(quest_id=1),)
    assert protected_profile_items(state,profile)==frozenset({123})

def test_verified_white_junk_is_sellable_but_quest_protection_remains():
    item=MerchantItem(0,1,123,1,1,8,0,False,'VENDOR_JUNK')
    s=NS(merchant_open=True,merchant_scan_complete=True,resources=object(),truncated=False,
         inventory_sale_scan=False,merchant_items=(item,))
    assert safe_sale_candidates(s,protected_item_ids=frozenset())==(item,)
    assert not safe_sale_candidates(s,protected_item_ids=frozenset({123}))

def test_intellect_falls_back_to_plain_cast_after_clearing_target():
    d=NS(chat=Mock(),emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    counter=0
    def cap():
        nonlocal counter
        counter+=1
        return NS(sequence=counter,truncated=False,
            player=NS(guid='aarr',dead=False,in_combat=False,target_guid='enemy' if counter==1 else ''),
            resources=NS(mana=200,max_mana=360),
            mage_buffs=NS(intellect_active=counter>=23,armor_active=True))
    s=restore_self_buff(state=cap(),capture=cap,driver=d,name='Arcane Intellect',
                        field='intellect_active',sleep=lambda _:None)
    assert s.mage_buffs.intellect_active
