"""Stationary HUD updates must reflect real checkpoints, not a fake heartbeat."""
from unittest.mock import Mock, patch

import pytest

from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.horde.hunt_retry import wait_for_patrol_refresh
from wotlkbuddy.horde.death_recovery import watch_reclaim_with_status


def test_work_updates_are_throttled_and_clear_immediately():
    driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
    driver.chat = Mock()
    driver.wow_is_foreground = lambda: True
    driver.emergency_stop_requested = lambda: False
    with patch('wotlkbuddy.horde.interaction.monotonic', side_effect=[0, 2, 6, 7]):
        driver.set_work_status('routes', 1, 48)
        driver.set_work_status('routes', 2, 48)
        driver.set_work_status('routes', 17, 48)
        driver.set_work_status('clear')
    assert [c.args[0] for c in driver.chat.call_args_list] == [
        '/wbtwork routes 1 48', '/wbtwork routes 17 48', '/wbtwork clear 0 0']


def test_work_updates_respect_focus_and_disabled_hud():
    driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
    driver.chat = Mock()
    driver.wow_is_foreground = lambda: False
    driver.emergency_stop_requested = lambda: False
    driver.set_work_status('routes', 1, 48)
    driver.wow_is_foreground = lambda: True
    driver.native_hud_enabled = False
    driver.set_work_status('reclaim')
    driver.chat.assert_not_called()
    with pytest.raises(ValueError):
        driver.set_work_status('arbitrary')


def test_patrol_wait_reports_start_and_clears_on_exit():
    driver = Mock()
    def sleep(seconds):
        assert seconds == 8
        driver.set_work_status.assert_called_once_with('patrol')
    wait_for_patrol_refresh(driver, sleep)
    assert [c.args for c in driver.set_work_status.call_args_list] == [('patrol',), ('clear',)]


def test_reclaim_wait_updates_only_on_returned_observation():
    from types import SimpleNamespace as NS
    work = Mock()
    sample = Mock(return_value=(NS(player=NS(dead=True), death_state=NS(ghost=True)), object()))
    def watch(*, sample, **kwargs):
        assert work.call_count == 1
        sample()
        assert work.call_count == 2
        return 'site'
    with patch('wotlkbuddy.horde.death_recovery.watch_reclaim_window', side_effect=watch):
        assert watch_reclaim_with_status(work=work, sample=sample) == 'site'
