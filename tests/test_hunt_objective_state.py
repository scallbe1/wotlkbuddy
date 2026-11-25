"""Independent hunt objective evidence and progress budget tests."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.hunt_objective_state import HuntObjectiveState
from wotlkbuddy.live.control import LiveControlError


def test_final_inventory_must_confirm_completed_journal():
    state = HuntObjectiveState(active_tracked_item=100, collected_results={100: (2, 4)})
    with pytest.raises(LiveControlError, match='final tracked'):
        state.finalize(NS(tracked_item_id=100, tracked_item_count=3), collect_items=True)
    assert state.finalize(NS(tracked_item_id=100, tracked_item_count=4),
                          collect_items=True) == ((100, 4, 4),)


def test_collection_without_authorized_sources_stops_before_input():
    driver = Mock()
    state = HuntObjectiveState()
    with pytest.raises(LiveControlError, match='no Mob source IDs'):
        state.prepare(object(), (NS(entry=100, sources=()), object()),
                      collect_items=True, profile=None, quest_id=364,
                      driver=driver, fresh=Mock(), log=Mock())
    driver.chat.assert_not_called()
    assert driver.active_quest_entries == ()


def test_live_kill_total_takes_precedence_over_xml():
    state = HuntObjectiveState()
    log = Mock()
    spec = NS(entry=123, count=8)
    observed = NS(description='Mob: 9/10')
    before = object()
    assert state.prepare(before, (spec, observed), collect_items=False,
                         profile=None, quest_id=364, driver=Mock(),
                         fresh=Mock(), log=log) == (before, spec, observed, (123,))
    assert '10 kills' in log.call_args.args[0]
    assert state.last_description == 'Mob: 9/10'


def test_second_kill_without_journal_credit_stops():
    state = HuntObjectiveState()
    args = dict(qlog=lambda _: NS(complete=False, objectives=(NS(description='Mob: 1/8'),)),
                collect_items=False, spec=NS(entry=123), observed=None,
                before_text=('Mob: 1/8',), log=Mock())
    state.after_loot(object(), **args)
    assert state.no_credit == 1
    with pytest.raises(LiveControlError, match='Two verified kills'):
        state.after_loot(object(), **args)


def test_journal_progress_resets_no_credit_budget():
    state = HuntObjectiveState(no_credit=1)
    state.after_loot(object(), qlog=lambda _: NS(complete=False,
                     objectives=(NS(description='Mob: 2/8'),)), collect_items=False,
                     spec=NS(entry=123), observed=None, before_text=('Mob: 1/8',), log=Mock())
    assert state.no_credit == 0


def test_collection_rejects_journal_credit_exceeding_inventory():
    state = HuntObjectiveState()
    with pytest.raises(LiveControlError, match='does not confirm'):
        state.after_loot(NS(tracked_item_id=100, tracked_item_count=1),
                         qlog=lambda _: NS(complete=False, objectives=(
                             NS(index=1, description='Item: 2/4'),)),
                         collect_items=True, spec=NS(entry=100, count=4),
                         observed=NS(index=1), before_text=('Item: 0/4',), log=Mock())
