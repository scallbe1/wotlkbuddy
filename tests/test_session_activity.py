"""Session activity ownership, interruption and terminal-state regressions."""
from argparse import Namespace
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.session_activity import Activity, ActivityConflict, ActivityGate
from wotlkbuddy.horde.session_controller import QuestSession
from wotlkbuddy.live.control import LiveControlError


def test_claim_rejects_overlapping_activity_and_releases_owner():
    gate = ActivityGate()
    with gate.claim(Activity.OBJECTIVES):
        assert gate.current is Activity.OBJECTIVES
        with pytest.raises(ActivityConflict):
            with gate.claim(Activity.TRAINING):
                pytest.fail('overlapping activity entered')
        assert gate.current is Activity.OBJECTIVES
    assert gate.current is Activity.IDLE


def test_exception_releases_claim_and_stopped_gate_cannot_restart():
    gate = ActivityGate()
    with pytest.raises(ValueError, match='interruption'):
        with gate.claim(Activity.OBJECTIVES):
            raise ValueError('interruption')
    assert gate.current is Activity.IDLE
    gate.finish()
    assert gate.current is Activity.STOPPED
    with pytest.raises(ActivityConflict):
        with gate.claim(Activity.RECOVERY):
            pytest.fail('stopped session restarted')


@pytest.mark.parametrize('mode,owner', [
    ('objectives', Activity.OBJECTIVES), ('quests', Activity.OFFERS),
    ('train', Activity.TRAINING),
])
def test_progress_owns_only_its_requested_activity(mode, owner):
    session = QuestSession(Namespace(), Mock(), Mock(), 0)
    def progress(*args, **kwargs):
        assert session.activity.current is owner
        return {}
    session.progress.side_effect = progress
    assert session.phase(mode) == {}
    assert session.activity.current is Activity.IDLE


def test_recovery_claim_begins_after_failed_objective_releases(monkeypatch):
    from time import monotonic
    from types import SimpleNamespace as NS
    monkeypatch.setattr('wotlkbuddy.horde.session_controller.capture_monitor',lambda _:NS(truncated=False,player=NS(guid='player')))
    session = QuestSession(Namespace(auto_minutes=180,monitor=0), Mock(), Mock(), monotonic())
    def progress(*args, **kwargs):
        assert session.activity.current is Activity.OBJECTIVES
        raise LiveControlError('interrupted')
    def recover(*args, **kwargs):
        assert session.activity.current is Activity.RECOVERY
        return {'status': 'combat_cleared'}
    session.progress.side_effect = progress
    session.recover.side_effect = recover
    assert session.guarded_phase('objectives', 1) is None
    assert session.activity.current is Activity.IDLE


def test_reentrant_progress_is_not_treated_as_combat_failure():
    session = QuestSession(Namespace(), Mock(), Mock(), 0)
    session.progress.side_effect = lambda *a, **kw: session.phase('train')
    with pytest.raises(ActivityConflict):
        session.guarded_phase('objectives', 1)
    session.recover.assert_not_called()
    assert session.activity.current is Activity.IDLE


@pytest.mark.parametrize('failure', [False, True])
def test_run_finishes_activity_gate_even_when_phase_fails(failure):
    import time
    session = QuestSession(Namespace(max_cycles=1, auto_minutes=5), Mock(), Mock(),
                           time.monotonic())
    if failure:
        session.progress.side_effect = RuntimeError('unexpected')
        with pytest.raises(RuntimeError, match='unexpected'):
            session.run()
    else:
        session.progress.return_value = {'quest_turnin': {'status': 'turned_in', 'quest_id': 364}}
        assert session.run()['quest_ids_turned_in'] == [364]
    assert session.activity.current is Activity.STOPPED
