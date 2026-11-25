"""Session training policy and post-hand-in verification tests."""
from argparse import Namespace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde import session_training as training
from wotlkbuddy.live.control import LiveControlError


@pytest.mark.parametrize('automatic,level,expected', [
    (False, 6, False), (True, 3, False), (True, 4, True),
])
def test_initial_training_priority(monkeypatch, automatic, level, expected):
    snapshot = NS(player=NS(guid='guid', name='Aarr', level=level),
                  resources=NS(money_copper=100, free_bag_slots=5))
    monkeypatch.setattr(training, 'estimated_vendor_copper', Mock(return_value=0))
    monkeypatch.setattr(training, 'load_goal', Mock(return_value=None))
    _, goal, due = training.training_priority(
        Namespace(_continuous_training_check=automatic),
        cap=lambda: snapshot, quest_profile=object(), log=Mock())
    assert goal is None
    assert due is expected


def test_no_training_due_does_not_visit_trainer(monkeypatch):
    monkeypatch.setattr(training, 'training_priority', Mock(return_value=(None, None, False)))
    visit = Mock()
    monkeypatch.setattr(training, 'visit_and_train', visit)
    training.execute_training_phase(
        Namespace(only='objectives'), quest_profile=None, profiles_root=None,
        cap=Mock(), driver=Mock(), location=Mock(), pid=1, nav=Mock(), db=Mock(),
        hud=Mock(), set_native_status=Mock(), serialize_vendor_sale=Mock(),
        report={}, log=Mock())
    visit.assert_not_called()


@pytest.mark.parametrize('history,active,valid', [
    (True, False, True), (False, False, False), (True, True, False),
])
def test_trainer_quest_requires_server_completion_and_journal_removal(history, active, valid):
    trainer = NS(entry=123, name='Trainer')
    profile = NS(steps=[NS(kind='turnin', entry=123, npc_name='Trainer', quest_id=364)])
    state = NS(quest_completions=((364, history),),
               quests=(NS(quest_id=364),) if active else ())
    report = {}
    kwargs = dict(quest_profile=profile, trainer=trainer, handed_in=(364,),
                  cap=lambda: state, hud=Mock(), report=report)
    if valid:
        training.verify_trainer_quests(**kwargs)
    else:
        with pytest.raises(LiveControlError, match='contradicted'):
            training.verify_trainer_quests(**kwargs)
    assert report['post_training_quest_verification']['364'] == {
        'server_history_complete': history, 'still_in_journal': active}
