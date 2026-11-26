"""Independent tests of continuous-session orchestration."""
from argparse import Namespace
from unittest.mock import Mock
from types import SimpleNamespace as NS
from time import monotonic

import pytest

from wotlkbuddy.horde.session_controller import QuestSession
from wotlkbuddy.live.control import LiveControlError


def session(progress=None, recover=None):
    return QuestSession(Namespace(auto_minutes=180,monitor=0), progress or Mock(), recover or Mock(), monotonic())


def test_phase_passes_training_state_without_mutating_parent_args():
    s = session()
    s.train_level = 6
    s.phase('objectives')
    child = s.progress.call_args.args[0]
    assert child.only == 'objectives'
    assert child._continuous_training_check is True
    assert child._last_training_level == 6
    assert not hasattr(s.args, 'only')
    assert s.progress.call_args.kwargs == {
        '_skip_countdown': True, '_emit_report': False}


@pytest.mark.parametrize('mode', ['objectives', 'quests'])
def test_both_phases_share_recovery_and_do_not_advance_on_error(mode,monkeypatch):
    monkeypatch.setattr('wotlkbuddy.horde.session_controller.capture_monitor',lambda _:NS(truncated=False,player=NS(guid='player')))
    error = LiveControlError('verified encounter interruption')
    s = session(Mock(side_effect=error), Mock(return_value={'status': 'combat_cleared'}))
    assert s.guarded_phase(mode, 3) is None
    s.recover.assert_called_once_with(s.args, original_error=error)
    assert s.aggro_recoveries == 1
    assert s.recoveries == 0
    assert s.steps == [{'cycle': 3, 'action': 'aggro_recovery',
                        'recovery': {'status': 'combat_cleared'}}]


def test_recovery_budget_propagates_original_error():
    s = session()
    s.recoveries = 3
    error = LiveControlError('failed')
    with pytest.raises(LiveControlError) as caught:
        s.recover_phase(error, 1)
    assert caught.value is error
    s.recover.assert_not_called()


def test_training_level_is_shared_with_next_phase():
    s = session(Mock(return_value={'trained_spells': ['Fireball']}))
    assert not s.handle_objectives({
        'quest_turnin': {'status': 'turned_in', 'quest_id': 364},
        'levels_gained_this_run': 1, 'character_level_after': 6}, 1)
    assert s.train_level == 6
    assert s.steps[-1] == {'cycle': 1, 'action': 'train', 'trained_spells': ['Fireball']}
