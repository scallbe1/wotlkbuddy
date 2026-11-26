from types import SimpleNamespace as NS
from unittest.mock import Mock
from wotlkbuddy.horde import session_inventory


def test_explicit_vendor_sale_even_with_spare_bag_slots(monkeypatch):
    state=NS(resources=NS(free_bag_slots=12))
    sale=Mock(return_value=None)
    monkeypatch.setattr(session_inventory,'free_bag_space',sale)
    inv=session_inventory.SessionInventory({})
    kwargs=dict(quest_profile=NS(min_free_bag_slots=2),profiles_root='profiles',
                pid=1,cap=lambda:state,driver=Mock(),nav=Mock(),db=Mock(),
                location=lambda:(state,None),hud=Mock(),set_native_status=Mock(),log=Mock())
    inv.ensure_reward_space(**kwargs)
    sale.assert_not_called()
    inv.ensure_reward_space(**kwargs,force_sale=True)
    assert sale.call_count==1
    assert sale.call_args.kwargs['force_sale'] is True
