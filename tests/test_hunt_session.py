"""Hunt session wiring and lifecycle progress are isolated across hunts."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.hunt_session import HuntProgress, wire_hunt_services
from wotlkbuddy.live.control import LiveControlError


def test_verified_kill_accounting_is_session_local():
    first, second = HuntProgress(), HuntProgress()
    first.record_kill(123)
    first.record_kill(124)
    assert (first.kills, first.last_target) == (2, 124)
    assert (second.kills, second.last_target) == (0, 0)


def test_objective_index_is_live_and_requires_observed_objective():
    progress = HuntProgress()
    with pytest.raises(LiveControlError, match='No observed hunt objective'):
        progress.objective_index()
    progress.observed = NS(index=1)
    assert progress.objective_index() == 1
    progress.observed = NS(index=2)
    assert progress.objective_index() == 2


def wire(driver):
    return wire_hunt_services(
        capture=Mock(), driver=driver, guid='character', quest_id=364,
        started=0, max_seconds=900, now=lambda: 0, sleep=Mock(), log=Mock(),
        pid=42, nav=Mock(), target_xyz=Mock(), world_position=Mock(),
        travel=Mock(), collect_items=False)


def test_wiring_reuses_driver_exclusions_but_not_session_counters():
    driver = NS(blocked_combat_guids={'blocked'})
    first, second = wire(driver), wire(driver)
    assert first.unhittable_guids is second.unhittable_guids is driver.blocked_combat_guids
    assert first.routes is not second.routes
    assert first.loot_stats is not second.loot_stats
    assert first.progress is not second.progress


def test_wiring_does_not_send_input_and_initializes_missing_exclusions():
    driver = NS(chat=Mock(), interact=Mock())
    services = wire(driver)
    assert services.unhittable_guids is driver.blocked_combat_guids
    assert not services.unhittable_guids
    driver.chat.assert_not_called()
    driver.interact.assert_not_called()
