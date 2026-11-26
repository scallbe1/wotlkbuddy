from types import SimpleNamespace as NS

import pytest

from wotlkbuddy.horde.merchant import MerchantUnavailable, verify_merchant_state
from wotlkbuddy.live.control import LiveControlError


def state(target_guid='', target_name='', opened=False):
    return NS(truncated=False, player=NS(guid='player', dead=False,
        in_combat=False,target_guid=target_guid,target_name=target_name),
        resources=NS(class_token='MAGE'),merchant_open=opened,dialog=None)


def verify(snapshot, *, started=False, focus=True):
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:focus)
    verify_merchant_state(snapshot,guid='player',merchant_guid='0xF130000DDB000001',
        npc_name='Hamlin Atkins',npc_id=3547,driver=driver,transaction_started=started)


def test_lost_merchant_before_sale_can_try_another():
    with pytest.raises(MerchantUnavailable,match='no transaction attempted'):
        verify(state())


def test_lost_target_during_sale_is_not_retryable():
    with pytest.raises(LiveControlError,match='refusing further merchant input') as exc:
        verify(state(),started=True)
    assert not isinstance(exc.value,MerchantUnavailable)


def test_lost_focus_is_not_a_merchant_retry():
    with pytest.raises(LiveControlError,match='lost foreground') as exc:
        verify(state(),focus=False)
    assert not isinstance(exc.value,MerchantUnavailable)


def test_same_entry_different_guid_is_rejected():
    with pytest.raises(MerchantUnavailable,match='target changed'):
        verify(state('0xF130000DDB000002','Hamlin Atkins'))


def test_slumber_sand_can_be_sold_but_quest_protection_wins():
    from wotlkbuddy.horde.merchant import safe_sale_candidates
    from wotlkbuddy.live.codec import MerchantItem
    sand=MerchantItem(0,1,3434,5,1,3,0,False,'VENDOR_JUNK')
    old_robe=MerchantItem(0,2,1234,1,1,10,0,False,'INVTYPE_ROBE')
    potion=MerchantItem(0,3,858,1,1,10,0,False,'')
    snapshot=NS(merchant_open=True,merchant_scan_complete=True,resources=NS(),
        truncated=False,inventory_sale_scan=False,merchant_items=(sand,old_robe,potion))
    assert safe_sale_candidates(snapshot,protected_item_ids=frozenset())==(sand,old_robe)
    assert safe_sale_candidates(snapshot,protected_item_ids=frozenset({3434}))==(old_robe,)
