"""Verify objective selection independently of the live client."""
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde import session_objectives as service


@pytest.mark.parametrize("mode,steps,expected", [
    ('all', (2, 1, 3), 'collect'),
    ('all', (1, 1, 1), 'gameobject'),
    ('all', (1, 2, 3), 'kill'),
    ('collect', (1, 2, 3), 'collect'),
    ('objects', (1, 2, 3), 'gameobject'),
])
def test_profile_order_and_mode_filtering(monkeypatch, mode, steps, expected):
    kill, collect, obj = [SimpleNamespace(profile_step=n, objective_type='CollectItem')
                          for n in steps]
    callbacks = [Mock(return_value=value) for value in (kill, collect, obj)]
    for name, callback in zip(('next_active_kill_quest', 'next_active_collect_quest',
                               'next_active_gameobject_quest'), callbacks):
        monkeypatch.setattr(service, name, callback)
    result = service.select_objective(Namespace(only=mode), object(), object(),
                                      SimpleNamespace(map_id=0))
    assert result[0] == expected
    assert result[1] is {'kill': kill, 'collect': collect, 'gameobject': obj}[expected]
    if mode in ('collect', 'objects'):
        callbacks[0].assert_not_called()
    if mode == 'objects':
        callbacks[1].assert_not_called()


def test_collect_mode_excludes_non_collection_gameobject(monkeypatch):
    monkeypatch.setattr(service, 'next_active_collect_quest', Mock(return_value=None))
    monkeypatch.setattr(service, 'next_active_gameobject_quest',
                        Mock(return_value=SimpleNamespace(objective_type='UseObject')))
    result = service.select_objective(Namespace(only='collect'), object(), object(),
                                      SimpleNamespace(map_id=0))
    assert result[:2] == (None, None)
