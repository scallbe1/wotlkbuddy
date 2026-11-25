"""Independent verification of attack evidence and cast observation."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.hunt_attack import (
    quest_credit_advanced, confirm_lost_target, observe_cast, observe_impact, attack_target)
from wotlkbuddy.live.control import LiveControlError


@pytest.mark.parametrize('description,index,expected', [
    ('Mob: 2/8', 1, True), ('Mob: 1/8', 1, False),
    ('Mob: 2/9', 1, False), ('Mob: 2/8', 2, False),
])
def test_only_exact_objective_credit_counts(description, index, expected):
    journal = NS(objectives=(NS(index=index, description=description),))
    assert quest_credit_advanced(
        object(), previous_credit=(1, 8), objective_index=1,
        qlog=lambda _: journal) is expected


def test_lost_kill_target_without_credit_never_records_kill():
    state = NS(player=NS(target_guid=''))
    record = Mock()
    fresh = Mock(return_value=state)
    with pytest.raises(LiveControlError, match='without confirmed'):
        confirm_lost_target(state, collect_items=False, target_guid='mob', expected=123,
                            driver=Mock(), fresh=fresh, credit_advanced=lambda _: False,
                            record_kill=record, log=Mock())
    record.assert_not_called()
    assert fresh.call_count == 4


def test_lost_kill_target_with_credit_records_once():
    state = NS(player=NS(target_guid=''))
    record = Mock()
    assert confirm_lost_target(
        state, collect_items=False, target_guid='mob', expected=123,
        driver=Mock(), fresh=Mock(), credit_advanced=lambda _: True,
        record_kill=record, log=Mock()) is state
    record.assert_called_once_with(123)


def test_collection_rejects_wrong_corpse_even_if_dead():
    before = NS(player=NS(target_guid=''))
    after = NS(player=NS(target_guid='other', in_combat=False),
               combat_target=NS(entry_id=123, dead=True))
    record = Mock()
    with pytest.raises(LiveControlError, match='exact dead corpse'):
        confirm_lost_target(before, collect_items=True, target_guid='mob', expected=123,
                            driver=Mock(), fresh=Mock(return_value=after),
                            credit_advanced=Mock(), record_kill=record, log=Mock())
    record.assert_not_called()


def test_observe_cast_waits_for_active_cast():
    before = object()
    during = NS(current_cast_remaining_ms=200, current_cast_name='Fireball',
                fireball_cast_remaining_ms=200)
    pending = NS(current_cast_remaining_ms=100, fireball_cast_remaining_ms=100,
                 player=NS(target_guid='mob'), combat_target=NS(dead=False))
    finished = NS(current_cast_remaining_ms=0, fireball_cast_remaining_ms=0,
                  player=NS(target_guid='mob'), combat_target=NS(dead=False))
    fresh = Mock(side_effect=[during, pending, finished])
    assert observe_cast(before, action=NS(name='Fireball'), target_guid='mob',
                        fresh=fresh) is finished
    assert fresh.call_count == 3


def test_impact_credit_returns_terminal_fight_result():
    state = NS(player=NS(target_guid='', in_combat=False), combat_target=None)
    record = Mock()
    assert observe_impact(object(), target_guid='mob', expected=123,
                          previous_health=50, fresh=Mock(return_value=state),
                          recover=Mock(), credit_advanced=lambda _: True,
                          record_kill=record, log=Mock()) == (state, None, True)
    record.assert_called_once_with(123)


def test_attack_rejects_unlearned_fireball_before_input():
    driver = Mock()
    state = NS(mage_buffs=NS(knows_fireball=False))
    with pytest.raises(LiveControlError, match='not confirmed learned'):
        attack_target(state, 123, 1, (0, 8), driver=driver, guard=Mock(),
                      fresh=Mock(), qlog=Mock(), clear_remaining_aggro=Mock(),
                      approach_unhittable_target=Mock(), collect_items=False,
                      record_kill=Mock(), log=Mock())
    assert not driver.mock_calls
