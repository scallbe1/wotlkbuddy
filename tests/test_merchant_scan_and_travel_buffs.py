from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.merchant import wait_merchant_scan, MerchantUnavailable
from wotlkbuddy.horde.session_startup import prepare_travel_resources


def test_open_merchant_waits_for_complete_scan():
    initial=NS(merchant_open=True,merchant_scan_complete=False,sequence=1)
    ready=NS(merchant_open=True,merchant_scan_complete=True,sequence=3)
    frames=iter([NS(merchant_open=True,merchant_scan_complete=False,sequence=2),ready])
    clock=[0.]
    messages=[]
    result=wait_merchant_scan(initial,capture=lambda:next(frames),guard=lambda _:None,
        sleep=lambda dt:clock.__setitem__(0,clock[0]+dt),now=lambda:clock[0],
        log=messages.append,npc_name='Constance')
    assert result is ready
    assert any('waiting for complete inventory scan' in m for m in messages)


def test_incomplete_merchant_scan_times_out_without_authorizing_sale():
    clock=[0.]
    seq=[0]
    def capture():
        seq[0]+=1
        return NS(merchant_open=True,merchant_scan_complete=False,sequence=seq[0])
    with pytest.raises(MerchantUnavailable,match='no further sale authorized'):
        wait_merchant_scan(capture(),capture=capture,guard=lambda _:None,
            sleep=lambda dt:clock.__setitem__(0,clock[0]+dt),now=lambda:clock[0],
            log=lambda _:None,npc_name='Constance',timeout=1)


def test_deferred_intellect_is_revisited_after_drinking(monkeypatch):
    def state(mana,intellect):
        return NS(player=NS(in_combat=False),resources=NS(mana=mana,max_mana=423),
            mage_buffs=NS(knows_intellect=True,intellect_active=intellect,
                          knows_frost_armor=True,armor_active=True))
    supplies=Mock(side_effect=[state(233,False),state(412,False),state(420,True)])
    buffs=Mock(side_effect=[state(176,False),state(339,True)])
    monkeypatch.setattr('wotlkbuddy.horde.sustain.maintain_mage_supplies',supplies)
    monkeypatch.setattr('wotlkbuddy.horde.recovery_rest.maintain_travel_buffs',buffs)
    result=prepare_travel_resources(cap=Mock(),driver=Mock(),log=Mock())
    assert result.mage_buffs.intellect_active
    assert buffs.call_count==2
    assert buffs.call_args.kwargs['state'].resources.mana==412
    assert supplies.call_count==3
    assert supplies.call_args.kwargs['restock'] is False
