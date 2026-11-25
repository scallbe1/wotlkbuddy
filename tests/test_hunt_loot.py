"""Quest-hunt loot safety and evidence tests."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.hunt_loot import (
    LootStats, verified_corpse, open_and_confirm_loot, take_loot_slots)


@pytest.mark.parametrize('guid,entry,dead,combat,expected', [
    ('mob', 123, True, False, True),
    ('other', 123, True, False, False),
    ('mob', 124, True, False, False),
    ('mob', 123, False, False, False),
    ('mob', 123, True, True, False),
])
def test_corpse_requires_exact_identity(guid, entry, dead, combat, expected):
    state = NS(player=NS(target_guid=guid, in_combat=combat),
               combat_target=NS(entry_id=entry, dead=dead))
    assert verified_corpse(state, dead_guid='mob', expected_entry=123) is expected


def test_item_autoloot_requires_inventory_and_journal_evidence():
    before = NS(tracked_item_count=1, tracked_item_id=100)
    after = NS(loot_open=False, tracked_item_id=100, tracked_item_count=2)
    stats = LootStats()
    driver = Mock()
    journal = NS(objectives=(NS(index=1, description='Item: 2/4'),))
    assert open_and_confirm_loot(
        before, dead_guid='mob', verified_corpse=lambda _: True,
        driver=driver, fresh=Mock(return_value=after), collect_items=True,
        tracked_item_id=lambda: 100, objective_index=lambda: 1,
        qlog=lambda _: journal, stats=stats, log=Mock()) == (after, False)
    assert (stats.attempts, stats.confirmed, stats.skipped) == (1, 1, 0)
    driver.interact.assert_called_once()


def test_slot_that_does_not_disappear_is_not_confirmed():
    state = NS(loot_count=1, loot_open=True)
    stats = LootStats()
    driver = Mock()
    assert take_loot_slots(state, driver=driver, fresh=Mock(return_value=state),
                           stats=stats, log=Mock()) is state
    assert (stats.confirmed, stats.skipped) == (0, 1)
    driver.chat.assert_called_once_with('/run LootSlot(1)')


def test_cleared_slots_are_confirmed():
    state = NS(loot_count=1, loot_open=True)
    after = NS(loot_count=0, loot_open=False)
    stats = LootStats()
    assert take_loot_slots(state, driver=Mock(), fresh=Mock(return_value=after),
                           stats=stats, log=Mock()) is after
    assert (stats.confirmed, stats.skipped) == (1, 0)
