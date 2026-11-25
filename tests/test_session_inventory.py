"""Inventory session coordination without live client input."""
from dataclasses import dataclass
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde import session_inventory as service


@dataclass
class Sale:
    copper_received: int


def test_record_sale_keeps_order_and_ignores_no_sale():
    report = {}
    inventory = service.SessionInventory(report)
    assert inventory.record_sale(None) is None
    assert report == {}
    assert inventory.record_sale(Sale(10)) == {'copper_received': 10}
    inventory.record_sale(Sale(20))
    assert report['vendor_sales'] == [{'copper_received': 10}, {'copper_received': 20}]


@pytest.mark.parametrize('free_slots,expected_sale', [(4, False), (3, True)])
def test_reward_space_visits_merchant_only_below_required_minimum(monkeypatch, free_slots, expected_sale):
    sell = Mock(return_value=Sale(10))
    monkeypatch.setattr(service, 'free_bag_space', sell)
    status = Mock()
    report = {}
    result = service.SessionInventory(report).ensure_reward_space(
        quest_profile=NS(min_free_bag_slots=3), profiles_root='profiles', pid=1,
        cap=Mock(), driver=Mock(), nav=Mock(), db=Mock(),
        location=lambda: (NS(resources=NS(free_bag_slots=free_slots)), None),
        hud=Mock(), set_native_status=status, log=Mock())
    if expected_sale:
        assert sell.call_args.kwargs['min_free_slots'] == 4
        status.assert_called_once_with('vendor')
        assert result == {'copper_received': 10}
    else:
        sell.assert_not_called()
        status.assert_not_called()
        assert result is None


def test_bags_are_equipped_before_gear(monkeypatch):
    calls = []
    monkeypatch.setattr(service, 'equip_bag_upgrades',
                        lambda **kw: calls.append('bags') or (100,))
    monkeypatch.setattr(service, 'equip_mage_upgrades',
                        lambda **kw: calls.append('gear') or (200,))
    report = {}
    service.SessionInventory(report).reconcile_upgrades(
        'objectives', cap=lambda: NS(gear_scan_complete=True), driver=Mock(), log=Mock())
    assert calls == ['bags', 'gear']
    assert report == {'equipped_bag_ids': (100,), 'equipped_item_ids': (200,)}


@pytest.mark.parametrize('combat,candidates,upgrade', [
    (True, (100,), False), (False, (), False), (False, (100,), True),
])
def test_encounter_loot_only_equips_bags_out_of_combat(monkeypatch, combat, candidates, upgrade):
    observed = NS(player=NS(in_combat=combat), bag_candidates=candidates)
    monkeypatch.setattr(service, 'loot_encounter', Mock(return_value=observed))
    equip = Mock()
    monkeypatch.setattr(service, 'equip_bag_upgrades', equip)
    refreshed = object()
    cap = Mock(return_value=refreshed)
    driver = NS()
    service.bind_encounter_loot(cap=cap, location=Mock(), driver=driver, nav=Mock(), pid=1, log=Mock())
    result = driver.drain_encounter_loot(object())
    assert result is (refreshed if upgrade else observed)
    assert equip.call_count == int(upgrade)
    assert cap.call_count == int(upgrade)
